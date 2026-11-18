"""ROS-independent fleet tick. Enqueue wire data at receipt; consume outputs after tick."""
from collections import deque
from dataclasses import dataclass, field
from math import inf, nextafter
import sqlite3
from threading import Lock

from . import geometry as g
from . import schemas
from .deadlock import DeadlockManager
from .decisions import Decisions
from .mission import MissionManager
from .route_planner import waypoints
from .resource_manager import MotionRequest, ResourceManager, RobotReport
from .robot_protocol import RobotProtocol, number
from .safety import Safety
from .stall import Stall
from .store import Store


@dataclass
class FleetOutput:
    commands: list[MotionRequest] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    acks: list[dict] = field(default_factory=list)
    state: dict | None = None
    storage_error: str | None = None


class FleetCore:
    """Inputs: UI topic names, status/pose/battery/docked/hazards with logical robot, or bed exit."""

    def __init__(self, config, store=None):
        if config.robot_link.mode != "HOSPITAL_NAV":
            raise NotImplementedError("ICD_GOAL_POSE 어댑터는 아직 구현되지 않았습니다.")
        self.config = config
        self.store = store if store is not None else Store(config.db_path)
        self.resources = ResourceManager(config)
        self.deadlock = DeadlockManager(self.resources)
        self.decisions = Decisions(self.resources.record)
        self.safety = Safety(config, self.resources.record, self.decisions)
        self.links = {r: RobotProtocol(c.ns, config.robot_link) for r, c in config.robots.items()}
        self.mission = MissionManager(config, self.resources, self.deadlock, self.decisions,
                                      self.safety, self.links, self.store.next_id)
        self.stalls = {r: Stall(config.stall) for r in config.robots}
        self.blocks = {}
        self.link_ok = dict.fromkeys(config.robots, False)
        self.inputs = deque()
        self.history = self.store.recent_finished()
        self.state_times = {}
        self._lock = Lock()
        self._latest_bed = None
        self._next_publish = None
        self._acks_seen = {}
        self._unsaved_events = []
        self._last_tick = float("-inf")

    def enqueue(self, topic, payload, stamp, *, robot=None):
        if topic in {"status", "pose", "battery", "docked", "hazards"} and robot not in self.links:
            raise ValueError("unknown robot")
        with self._lock:
            if topic == "/patient/bed_exit" and type(payload) is bool:
                self._latest_bed = payload
            self.inputs.append((topic, payload, stamp, robot))

    def service(self, name, now):
        if name not in ("/robot/pause", "/robot/resume"):
            raise ValueError("unknown service")
        with self._lock:
            reason = self.safety.denial(self.links, now, self._latest_bed) if name == "/robot/resume" else None
            self.inputs.append(("resume_rejected" if reason else name, reason, now, None))
        return (False, reason) if reason else (True, "접수했습니다")

    def _command(self, cmd, now):
        action, robot = cmd["cmd"], cmd.get("robot")
        if action == "PAUSE_ROBOT":
            self.safety.pause(robot, "MANUAL", now)
        elif action == "RESUME_ROBOT":
            self.safety.resume([robot], now, individual=True)
        elif action == "RESOURCE_CLEAR_CONFIRM":
            self.resources.clear_unknown(cmd["resource"], cmd["by"], now)
        elif action == "CONFIRM_STEP":
            self.mission.confirm(robot, cmd["mission_id"], cmd["result"], now)
        elif action == "CANCEL_MISSION":
            self.mission.cancel(cmd["mission_id"], now)
        elif action == "RETRY":
            self.mission.retry(robot, now)
            self.decisions.resolve(robot, now, {"NAV_FAILED", "MOVE_REJECTED", "BLOCKED"})
            self._unblock(robot, now)
        elif action == "GO_ZONE":
            r = self.mission.robots[robot]
            if r.job or r.queue or r.canceling:
                raise ValueError("E-409: 임무 없는 로봇만 직접 이동할 수 있습니다.")
            return self.mission.submit({"type": "GO_ZONE", "level": self.config.mission.manual_level,
                                        "stops": [{"zone": cmd["zone"]}]}, now, manual_robot=robot)

    def _choose(self, data, now):
        key, choice, by = data["card_id"], data["choice"], data["by"]
        if self.deadlock.card and key == self.deadlock.card["card_id"]:
            self.deadlock.choose(key, choice, by, now)
            return
        card = self.decisions.validate(key, choice)
        if card["kind"] == "BED_MONITOR_LOST":
            self.safety.monitor.choose(choice, by, now)
            return
        robot = card["robots"][0]
        r = self.mission.robots[robot]
        if not r.job or r.canceling:
            raise ValueError("E-409: 현재 임무에서 실행할 수 없습니다.")
        if self.safety.causes[robot] and choice not in ("cancel", "hold"):
            raise ValueError("E-409: 안전 정지 중입니다.")
        kind = card["kind"]
        if choice == "cancel":
            # Check the corridor lock before consuming the operator's decision.
            self.mission.cancel(r.job.id, now, selected_card=key)
        elif kind == "STOP_MISMATCH":
            self.decisions.close(key, now, "selected", choice=choice, by=by)
            if choice == "skip":
                self.mission.advance(robot, now)
            return
        elif kind == "BLOCKED":
            if choice == "retry":
                self._unblock(robot, now, close_card=False)
            if choice == "hold":
                r.state, r.fault_stop = "FAULT", True
                for kind in ("FAULT", "PAUSE"):
                    self.resources.record(kind, now, "운영자가 막힌 임무를 보류했습니다.",
                                          robot=robot, mission_id=r.job.journal_id)
            elif choice == "retry" and self.links[robot].state == "PAUSED":
                r.state, r.resume_due = "MOVING", True
            else:
                r.state = "FAULT"
                self.mission.retry(robot, now, back=choice == "return")
        else:
            self.mission.retry(robot, now, back=choice == "return")
        self.decisions.close(key, now, "selected", choice=choice, by=by)

    def _ui(self, topic, data, now, out):
        ref_key = {"/ui/mission_req": "req_id", "/ui/operator_cmd": "cmd_id", "/ui/decision": "card_id"}[topic]
        ref = data[ref_key]
        cache_key = topic, ref
        if topic != "/ui/decision" and cache_key in self._acks_seen:
            out.acks.append(self._acks_seen[cache_key])
            return
        ack = {"v": 1, "ref": ref, "ok": True, "code": None, "mission_id": data.get("mission_id"),
               "message": "접수했습니다"}
        try:
            if topic == "/ui/mission_req":
                ack["mission_id"] = self.mission.submit(data, now)
            elif topic == "/ui/operator_cmd":
                ack["mission_id"] = self._command(data, now) or ack["mission_id"]
            else:
                self._choose(data, now)
        except ValueError as exc:
            code, _, message = str(exc).partition(":")
            ack.update(ok=False, code=code if code in schemas.ERROR_CODES else "E-400",
                       message=message.strip() or str(exc))
        if topic != "/ui/decision":
            self._acks_seen[cache_key] = ack
        out.acks.append(ack)

    def _unblock(self, robot, now, *, close_card=True):
        self.stalls[robot].reset()
        if robot not in self.blocks:
            return
        name = self.blocks.pop(robot)
        if name in self.resources.resources:
            res = self.resources.resources[name]
            res.stall_blockers.discard(robot)
            if not res.stall_blockers:
                res.blocked_since = None
        self.resources.record("UNBLOCKED", now, "막힘이 해제됐습니다.", robot=robot, region=name)
        if close_card:
            self.decisions.resolve(robot, now, {"BLOCKED"})

    def _stall_tick(self, robot, now, terminal, accepted):
        r, link = self.mission.robots[robot], self.links[robot]
        if (not r.job or (r.state != "MOVING" and not accepted) or not self.link_ok[robot] or self.safety.causes[robot]
                or self.resources.robot_state(robot, r.state) == "GIVE_WAY" or terminal):
            if terminal or not r.job:
                self._unblock(robot, now)
            else:
                self.stalls[robot].reset()
                self.stalls[robot].long = robot in self.blocks
            return
        if link.state != "MOVING" and robot not in self.blocks:
            self.stalls[robot].reset()
            return
        was_short = self.stalls[robot].short
        signal = self.stalls[robot].tick(now, link.pose, link.percent)
        if signal == "UNBLOCKED":
            self._unblock(robot, now)
        elif signal:
            reason = f"{robot}이 {self.stalls[robot].seconds:g}초 동안 진행하지 못했습니다."
            if signal == "STALL_LONG" and not was_short:
                self.resources.record("STALL_SHORT", now, reason, robot=robot, mission_id=r.job.journal_id)
            self.resources.record(signal, now, reason, robot=robot, mission_id=r.job.journal_id)
            if signal == "STALL_LONG":
                region = self.config.corridor.region_of(link.pose)
                self.blocks[robot] = region
                if region in self.resources.resources:
                    res = self.resources.resources[region]
                    if not res.stall_blockers:
                        res.blocked_since = now
                    res.stall_blockers.add(robot)
                self.resources.record("BLOCKED", now, reason, robot=robot, mission_id=r.job.journal_id, region=region)
                self.decisions.open("BLOCKED", robot, r.job.journal_id, reason, now)

    def _drain(self, now, out):
        with self._lock:
            batch, self.inputs = self.inputs, deque()
        ui, safety, messages = [], [], []
        parsers = {"/ui/mission_req": schemas.parse_mission_req, "/ui/decision": schemas.parse_decision,
                   "/ui/operator_cmd": lambda s: schemas.parse_operator_cmd(s, robots=self.links)}
        for topic, payload, stamp, robot in batch:
            if topic in parsers:
                try:
                    data = parsers[topic](payload)
                except schemas.SchemaError as exc:
                    out.acks.append(exc.ack())
                    if topic == "/ui/mission_req":
                        self.resources.record("MISSION_REJECTED", now, str(exc), ref=exc.ref, code=exc.code)
                    continue
                if topic == "/ui/operator_cmd" and data["cmd"] in {"PAUSE_ROBOT", "RESUME_ROBOT"}:
                    safety.append((topic, data, robot, stamp))
                else:
                    ui.append((topic, data))
            elif topic == "status":
                message = self.links[robot].receive(payload, stamp)
                if message:
                    messages.append((robot, message))
            elif topic == "pose":
                self.links[robot].update_pose(payload, stamp)
            elif topic == "battery":
                self.links[robot].battery = number(payload)
            elif topic == "docked":
                if type(payload) is bool:
                    self.links[robot].docked = payload
            elif topic in ("/patient/bed_exit", "/robot/pause", "/robot/resume", "resume_rejected", "hazards"):
                safety.append((topic, payload, robot, stamp))
            else:
                raise ValueError(f"unknown input {topic}")
        for robot, link in self.links.items():
            online = link.online(now)
            if online != self.link_ok[robot]:
                self.resources.record("LINK_BACK" if online else "LINK_LOST", now, "STATE 수신 상태 변경", robot=robot)
            self.link_ok[robot] = online
        return ui, safety, messages

    def tick(self, now):
        if now < self._last_tick:
            raise ValueError("tick time must be nondecreasing")
        self._last_tick = now
        out = FleetOutput()
        ui, safety, messages = self._drain(now, out)
        active = [robot for robot, unit in self.mission.robots.items() if unit.job]
        for topic, payload, _, stamp in safety:
            if topic == "/patient/bed_exit" and type(payload) is bool:
                self.safety.observe(payload, active, stamp)
        self.safety.monitor.tick(now)
        for robot in active:
            self.safety.protect(robot, now)
        for topic, payload, robot, _ in safety:
            if topic == "hazards":
                self.safety.hazards(robot, payload, now)
            elif topic == "/ui/operator_cmd":
                self._ui(topic, payload, now, out)
            elif topic == "/robot/pause":
                for r in self.links:
                    self.safety.pause(r, "MANUAL", now)
            elif topic == "/robot/resume":
                try:
                    self.safety.resume(self.links, now, individual=False)
                except ValueError:
                    pass  # rejected by safety; the event includes the reason
            elif topic == "resume_rejected":
                self.resources.record("RESUME_REJECTED", now, payload)
        terminal = {r for r, msg in messages if msg["event"] in {"MISSION_FAILED", "MISSION_CANCELED", "ARRIVED"}
                    or (msg["event"] == "CANCEL_CONFIRMED" and self.mission.robots[r].canceling)}
        accepted = {r for r, msg in messages if msg["event"] == "MISSION_ACCEPTED"}
        for robot in self.links:
            self._stall_tick(robot, now, robot in terminal, robot in accepted)

        self.resources.begin_tick()
        for robot, message in messages:
            self.mission.received(robot, message, now)
        choices, clears = [], []
        for topic, data in ui:
            if topic == "/ui/decision":
                choices.append(data)
            elif topic == "/ui/operator_cmd" and data["cmd"] == "RESOURCE_CLEAR_CONFIRM":
                clears.append(data)
            else:
                self._ui(topic, data, now, out)
        for robot in tuple(self.blocks):
            if self.mission.robots[robot].job is None:
                self._unblock(robot, now)
        self.mission.plan(now)
        reports = {}
        for robot, link in self.links.items():
            r = self.mission.robots[robot]
            reports[robot] = RobotReport(link.pose, self.link_ok[robot], bool(self.safety.causes[robot]),
                                         can_yield=r.state != "FAULT" and not r.canceling and not r.replacing)
        self.resources.observe(now, reports)
        for data in clears:
            self._ui("/ui/operator_cmd", data, now, out)
        arbitration = self.resources.arbitrate(now)
        for leg in arbitration.grants:
            self.mission.granted(leg)
        self.deadlock.observe(now)
        for data in choices:
            self._ui("/ui/decision", data, now, out)

        self.deadlock.expire(now)
        motions = {request.robot: request.command for request in arbitration.commands}
        for robot in self.links:
            command = self.mission.wire_step(robot, now, motions.get(robot))
            if command:
                out.commands.append(MotionRequest(robot, command))

        out.events = list(self.resources.events)
        out.cards = [e["detail"]["card"] for e in out.events if e["type"] == "CARD_OPEN"]
        self.resources.events.clear()
        self._unsaved_events.extend(out.events)
        for job in self.mission.dirty.values():
            if job.finished_at is not None and job.kind in schemas.MISSION_TYPES:
                self.history[job.id] = self.mission_row(job)
        while len(self.history) > 20:
            self.history.pop(next(iter(self.history)))
        try:
            self.store.save(self.mission.dirty.values(), self._unsaved_events)
        except sqlite3.OperationalError as exc:
            # Deliver already decided safety commands; retry the atomic journal on the next tick.
            out.storage_error = str(exc)
        else:
            self.mission.dirty.clear()
            self._unsaved_events.clear()
        for robot, r in self.mission.robots.items():
            state = self.visible_state(robot, r)
            if self.state_times.get(robot, (None,))[0] != state:
                self.state_times[robot] = (state, now)
        if self._next_publish is None:
            self._next_publish = now
        due = nextafter(now, inf) - self._next_publish
        if due >= 0:
            period = 1 / self.config.publish.fleet_state_hz
            self._next_publish += (int(due / period) + 1) * period
            out.state = self.snapshot(now)
        return out

    def visible_state(self, robot, r):
        base = r.state
        if base != "FAULT" and robot in self.deadlock.yielding:
            base = "YIELDING"
        elif base != "FAULT" and r.returning:
            base = "RETURNING"
        return self.resources.robot_state(robot, base) if self.link_ok[robot] else "OFFLINE"

    def mission_row(self, job):
        reason = self.resources.wait_reasons.get(job.robot) if job.status == "ACTIVE" else None
        if job.status == "QUEUED":
            reason = "쉬는 로봇 없음" if job.robot is None else "담당 로봇 대기"
        return {"mission_id": job.id, "type": job.kind, "level": job.level, "robot": job.robot,
                "status": job.status, "stops": [s["zone"] for s in job.stops], "stop_index": job.index,
                "patient": job.patient, "items": [s.get("item") for s in job.stops], "requested_by": job.requested_by,
                "created_at": job.created_at, "finished_at": job.finished_at, "wait_reason": reason,
                "requesting_department": job.department}

    def snapshot(self, now):
        robots = {}
        missions = [self.mission_row(job) for job in self.mission.unassigned]
        for robot, r in self.mission.robots.items():
            link = self.links[robot]
            region = self.config.corridor.region_of(link.pose) if link.pose is not None else None
            state = self.visible_state(robot, r)
            leg = self.resources.active.get(robot) or self.resources.pending.get(robot)
            causes = self.safety.causes[robot]
            robots[robot] = {
                "label": self.config.robots[robot].label, "ns": link.ns, "link_ok": self.link_ok[robot],
                "route": [list(p) for p in (link.pose, *waypoints(self.config, link.pose, leg.destination))] if leg and link.pose is not None else [],
                "last_state_at": link.state_at if link.state_at != float("-inf") else None,
                "state_since": self.state_times.get(robot, (None, now))[1],
                "state": state, "part4_state": link.state, "last_event": link.last_event,
                "region": region, "zone": r.zone if region == g.ZONE_REGIONS[r.zone] else None,
                "pose": None if link.pose is None else dict(zip(("x", "y"), link.pose)),
                "battery_pct": link.battery, "docked": link.docked, "mission_id": r.job.journal_id if r.job else None,
                "leg": {"from": r.zone, "to": leg.destination} if leg else None,
                "wait_reason": self.resources.wait_reasons.get(robot), "progress_pct": link.percent,
                "remaining_m": link.remaining_m, "stall_s": self.stalls[robot].seconds,
                "paused_by": sorted(causes)[0] if causes else ("CORRIDOR_YIELD" if state == "GIVE_WAY" else None),
            }
            jobs = ([r.job] if r.job else []) + list(r.queue)
            missions.extend(self.mission_row(j) for j in jobs if j.kind in schemas.MISSION_TYPES)
        cards = list(self.decisions.cards)
        if self.deadlock.card:
            cards.append(self.deadlock.card["card_id"])
        return {"v": 1, "stamp": now, "robots": robots, "resources": self.resources.snapshot(),
                "missions": missions + list(reversed(self.history.values())), "last_decision": self.resources.last_decision, "open_cards": cards,
                "bed_exit": {"value": self.safety.bed_exit, "since": self.safety.since,
                             "monitor": self.safety.monitor.snapshot(now)},
                "kpi": self.store.stats.kpi()}
