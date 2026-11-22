"""ROS-independent fleet tick. Enqueue wire data at receipt; consume outputs after tick."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from math import inf, nextafter
import json
import sqlite3
from threading import Lock

from . import geometry as g
from . import obstacle, schemas
from .mission import MissionManager
from .obstacle_report import ObstacleReports
from .legs import MotionRequest, RobotReport
from .plan_graph import door_route
from .robot_state import uses_crossroad
from .robot_protocol import PREPARING, RobotProtocol
from .safety import Safety
from .stall import Stall
from .store import Store
from .traffic import Traffic


@dataclass
class FleetOutput:
    inspections: list[str] = field(default_factory=list)
    commands: list[MotionRequest] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    acks: list[dict] = field(default_factory=list)
    state: dict | None = None
    storage_error: str | None = None


class FleetCore:
    """Inputs: UI topic names, status/pose/battery/docked/hazards with logical robot, or bed exit."""

    def __init__(self, config, store=None, *, plan_thread=False):
        """``plan_thread``: the plan's search runs in its own thread (the node); tests run it in the tick and still
        use its result in the next tick (plan_runner)."""
        self.config = config
        self.store = store if store is not None else Store(config.db_path)
        self.links = {r: RobotProtocol(c.ns, config.robot_link) for r, c in config.robots.items()}
        self.traffic = Traffic(config, self.links, plan_thread=plan_thread)
        self.resources = self.traffic.resources
        self.decisions = self.traffic.decisions
        self.collision = self.traffic.collision
        self.safety = Safety(config, self._safety_event, self.decisions)
        self.mission = MissionManager(config, self.resources, self.decisions,
                                      self.safety, self.links, self.store.next_id, traffic=self.traffic)
        self.stalls = {r: Stall(config.stall) for r in config.robots}
        self.inspect_inflight = {}
        self.blocks = {}
        self.block_legs = {}        # blocked robot -> the leg it was blocked on (a block holds that leg only)
        self.reports = ObstacleReports(self)
        self.mission.defer_failure = self.reports.defer
        self.link_ok = dict.fromkeys(config.robots, False)
        self.inputs = deque()
        self.history = self.store.recent_finished(config.publish.finished_mission_limit)
        self.state_times = {}
        self._lock = Lock()
        self._next_publish = None
        self._acks_seen = OrderedDict()
        self._unsaved_events = []
        self._pending_acks = []
        self._last_tick = float("-inf")

    def enqueue(self, topic, payload, stamp, *, robot=None):
        if topic in {"status", "pose", "battery", "docked", "hazards", "obstacle_result"} and robot not in self.links:
            raise ValueError("unknown robot")
        with self._lock:
            self.inputs.append((topic, payload, stamp, robot))

    def _safety_event(self, kind, now, reason, *, robot=None, **detail):
        job = self.mission.robots[robot].job if robot else None
        self.resources.record(kind, now, reason, robot=robot, mission_id=job.journal_id if job else None, **detail)

    def service(self, name, now):
        if name not in ("/robot/pause", "/robot/resume"):
            raise ValueError("unknown service")
        with self._lock:
            reason = None
            if name == "/robot/resume":
                # Preview pending readings without consuming them or producing safety events.
                signal = self.safety.exit_signal
                for topic, payload, stamp, _ in self.inputs:
                    if topic == "/patient/bed_exit" and type(payload) is bool:
                        signal = signal.after_sample(payload, stamp, self.safety.monitor.policy)
                reason = self.safety.denial(self.links, now, signal.value)
            self.inputs.append(("resume_rejected" if reason else name, reason, now, None))
        return (False, reason) if reason else (True, "접수했습니다")

    def _command(self, cmd, now):
        action, robot = cmd["cmd"], cmd.get("robot")
        if action == "PAUSE_ALL":
            for target in self.links:
                self.safety.pause(target, "MANUAL", now, by=cmd["by"])
        elif action == "RESUME_ALL":
            self.safety.resume(self.links, now, individual=False, by=cmd["by"])
        elif action == "PAUSE_ROBOT":
            self.safety.pause(robot, "MANUAL", now, by=cmd["by"])
        elif action == "RESUME_ROBOT":
            self.safety.resume([robot], now, individual=True, by=cmd["by"])
        elif action == "RESOURCE_CLEAR_CONFIRM":
            self.resources.clear_unknown(cmd["resource"], cmd["by"], now)
        elif action == "CONFIRM_STEP":
            self.mission.confirm(robot, cmd["mission_id"], cmd["result"], now, by=cmd["by"])
        elif action == "CANCEL_MISSION":
            self.mission.cancel(cmd["mission_id"], now, by=cmd["by"])
        elif action == "RETRY":
            self.mission.retry(robot, now)
            self.decisions.resolve(robot, now, {"NAV_FAILED", "MOVE_REJECTED", "BLOCKED"})
            self._unblock(robot, now)
        elif action == "GO_ZONE":
            r = self.mission.robots[robot]
            if r.job or r.queue or r.canceling:
                raise ValueError("E-409: 임무 없는 로봇만 직접 이동할 수 있습니다.")
            level = cmd.get("level", self.config.mission.manual_level)   # quick send: Level 1~3, else manual_level
            return self.mission.submit({"type": "GO_ZONE", "level": level,
                                        "requested_by": cmd["by"], "stops": [{"zone": cmd["zone"]}]}, now, manual_robot=robot)
        elif action == "SET_LEVEL":
            self.mission.set_level(cmd["mission_id"], cmd["level"], now, by=cmd["by"])
            return cmd["mission_id"]
        elif action == "BED_MONITOR_SET":
            self.safety.set_monitor(cmd["enabled"], now, by=cmd["by"])

    def _choose(self, data, now):
        key, choice, by = data["card_id"], data["choice"], data["by"]
        card = self.decisions.validate(key, choice)
        if card["kind"] == "BED_MONITOR_LOST":
            self.safety.monitor.choose(choice, by, now)
            return
        if card["kind"] == "STOP_UNCONFIRMED":
            # [다시 보내기] starts a new round of PAUSE in this tick; [현장에서 멈춘 것 확인] sends no more.
            if choice == "resend":
                self.links[card["robots"][0]].resend_pause()
            self.decisions.close(key, now, "selected", choice=choice, by=by)
            return
        robot = card["robots"][0]
        r = self.mission.robots[robot]
        if (not r.job and card.get("context") != "PROXIMITY") or r.canceling:
            raise ValueError("E-409: 현재 임무에서 실행할 수 없습니다.")
        if self.safety.causes[robot] and choice not in ("cancel", "hold"):
            raise ValueError("E-409: 안전 정지 중입니다.")
        kind = card["kind"]
        if card.get("context"):
            self.collision.choose(card, choice, now, self.mission, by)
        elif choice == "cancel":
            # Check the corridor lock before consuming the operator's decision.
            self.mission.cancel(r.job.id, now, selected_card=key, by=by)
        elif kind == "STOP_MISMATCH":
            self.decisions.close(key, now, "selected", choice=choice, by=by)
            r.job.stop_log[r.job.index]["status"] = "SKIPPED" if choice == "skip" else "ARRIVED"
            self.mission.dirty[r.job.id] = r.job
            if choice == "skip":
                self.mission.advance(robot, now)
            return
        elif kind == "BLOCKED":
            # The operator's choice reaches the plan as one of two inputs: the block is lifted (retry: the way is
            # clear) or a new goal (return: a new leg to the previous zone, mission.retry). A block holds only the
            # leg it was found on (_stalled), so the robot is planned again either way.
            if choice == "retry":
                self._unblock(robot, now, close_card=False)
            if choice == "hold":
                r.state, r.fault_stop = "FAULT", True
                for event_type in ("FAULT", "PAUSE"):
                    self.resources.record(event_type, now, "운영자가 막힌 임무를 보류했습니다.",
                                          robot=robot, mission_id=r.job.journal_id, by=by)
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
            self._acks_seen.move_to_end(cache_key)
            out.acks.append(self._acks_seen[cache_key])
            return
        ack = {"v": 1, "ref": ref, "ok": True, "code": None, "mission_id": data.get("mission_id"),
               "message": "접수했습니다"}
        try:
            if topic == "/ui/mission_req":
                ack["mission_id"] = self.store.mission_for_request(ref) or self.mission.submit(data, now)
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
            if len(self._acks_seen) > 500:
                self._acks_seen.popitem(last=False)
        out.acks.append(ack)

    def _unblock(self, robot, now, *, close_card=True, moved=False):
        if not moved:
            self.stalls[robot].reset()
        if robot not in self.blocks:
            return
        name = self.blocks.pop(robot)
        self.block_legs.pop(robot, None)
        self.traffic.unblock_robot(robot)
        self.resources.record("UNBLOCKED", now, "막힘이 해제됐습니다.", robot=robot, region=name)
        if close_card:
            for card in list(self.decisions.cards.values()):
                if card["kind"] == "BLOCKED" and not card.get("context") and robot in card["robots"]:
                    self.decisions.close(card["card_id"], now, "resolved")

    def _leg(self, robot):
        return self.resources.active.get(robot) or self.resources.pending.get(robot)

    def _stalled(self, robot):
        """Not moving for the plan: a block holds the leg it was found on; a new leg (a new goal) is planned again
        while the screen keeps the block until the robot moves. Without a block: stall.window_s without progress."""
        if robot in self.blocks:
            return self._leg(robot) is self.block_legs.get(robot)
        return self.stalls[robot].seconds >= self.config.stall.window_s

    def _inspect(self, robot, now, results, out, *, moving):
        stall, policy = self.stalls[robot], self.config.obstacle
        if policy.mode != "inspect":
            return  # report mode: PART 04 sends the photo itself (obstacle_report.py)
        job = self.mission.robots[robot].job
        context = dict(robot=robot, mission_id=job.journal_id if job else None, stall_s=stall.seconds)
        def record(result, applied):
            text = (obstacle.description(result, self.links[robot].pose, policy) if applied
                    else "현재 멈춤의 판단에서 제외한 분류 응답")
            self.resources.record("OBSTACLE_RESULT", now, text, **context, **result, applied=applied)
            if applied:
                stall.obstacle = result

        sent = self.inspect_inflight.get(robot)
        for stamp, payload in results:
            answer = obstacle.classify(payload, policy, stamp)
            in_window = sent is not None and sent <= stamp <= sent + policy.timeout_s
            record(answer, in_window and moving and sent == stall.requested_at)
            if in_window:
                self.inspect_inflight.pop(robot)
                sent = None

        if sent is not None and now >= sent + policy.timeout_s:
            if moving and sent == stall.requested_at:
                record({"kind": "UNKNOWN", "labels": [], "detections": [], "at": now}, True)
            self.inspect_inflight.pop(robot)
        if (moving and policy.enabled and robot not in self.inspect_inflight and stall.requested_at is None
                and stall.seconds >= policy.inspect_at_s):
            stall.requested_at = self.inspect_inflight[robot] = now
            out.inspections.append(robot)
            self.resources.record("OBSTACLE_INSPECT", now, "로봇 앞 장애물 촬영 요청", **context)

    def _stall_tick(self, robot, now, terminal, accepted, results, out):
        r, link = self.mission.robots[robot], self.links[robot]
        plan_hold = self.traffic.plan_hold(robot)
        if (not r.job or link.state in PREPARING or (r.state != "MOVING" and not accepted)
                or not self.link_ok[robot] or self.safety.causes[robot]
                or robot in self.collision.contacts or robot in self.collision.access.waits
                or plan_hold or terminal):
            self._inspect(robot, now, results, out, moving=False)
            if terminal or not r.job:
                self._unblock(robot, now)
            elif robot not in self.blocks:
                self.stalls[robot].reset()
            return
        if link.state not in {"MOVING", "DOCKING"} and robot not in self.blocks:
            self._inspect(robot, now, results, out, moving=False)
            self.stalls[robot].reset()
            return
        was_short = self.stalls[robot].short
        signal = self.stalls[robot].tick(now, link.pose, link.percent)
        stall = self.stalls[robot]
        self._inspect(robot, now, results, out, moving=True)
        if (stall.obstacle and stall.obstacle["kind"] == "OBJECT" and self.config.obstacle.object_blocks_now
                and not stall.long):
            stall.short = stall.long = True
            signal = "STALL_LONG"
        if signal == "UNBLOCKED":
            self._unblock(robot, now, moved=True)
        elif signal:
            reason = f"{self.resources.robot_label(robot)} — {stall.seconds:.1f}초 동안 이동 없음"
            if signal == "STALL_LONG" and not was_short:
                self.resources.record("STALL_SHORT", now, reason, robot=robot, mission_id=r.job.journal_id)
            if signal == "STALL_LONG" and stall.obstacle:
                reason += " · " + obstacle.description(stall.obstacle, link.pose, self.config.obstacle)
            self.resources.record(signal, now, reason, robot=robot, mission_id=r.job.journal_id, stall_s=stall.seconds)
            if signal == "STALL_LONG":
                region = self.traffic.block_robot(robot, link.pose, now)
                self.blocks[robot] = region
                self.block_legs[robot] = self._leg(robot)
                self.resources.record("BLOCKED", now, reason, robot=robot, mission_id=r.job.journal_id, region=region)
                self.decisions.open("BLOCKED", robot, r.job.journal_id, reason, now,
                                    obstacle_kind=stall.obstacle["kind"] if stall.obstacle else None)

    def _drain(self, now, out):
        batch, self.inputs = self.inputs, deque()
        ui, safety, messages = [], [], []
        results = {robot: [] for robot in self.links}
        allowed_resources = tuple(dict.fromkeys((*g.RESOURCES, *self.resources.segments)))
        parsers = {"/ui/mission_req": schemas.parse_mission_req, "/ui/decision": schemas.parse_decision,
                   "/ui/operator_cmd": lambda s: schemas.parse_operator_cmd(s, robots=self.links, resources=allowed_resources)}
        for topic, payload, stamp, robot in batch:
            if topic in parsers:
                try:
                    data = parsers[topic](payload)
                except schemas.SchemaError as exc:
                    out.acks.append(exc.ack())
                    if topic == "/ui/mission_req":
                        try:
                            raw = json.loads(payload)
                        except (ValueError, TypeError, RecursionError):
                            raw = None
                        by = raw.get("requested_by") if isinstance(raw, dict) else None
                        self.resources.record("MISSION_REJECTED", now, exc.user_message(), ref=exc.ref, code=exc.code,
                                              by=by if isinstance(by, str) and by.strip() else None)
                    continue
                if topic == "/ui/operator_cmd" and data["cmd"] in {"PAUSE_ROBOT", "RESUME_ROBOT", "PAUSE_ALL", "RESUME_ALL"}:
                    safety.append((topic, data, robot, stamp))
                else:
                    ui.append((topic, data))
            elif topic == "obstacle_result":
                if not obstacle.is_yolo_result(payload):
                    results[robot].append((stamp, payload))
            elif topic == "status":
                message = self.links[robot].receive(payload, stamp)
                if message:
                    messages.append((robot, message))
            elif topic == "pose":
                self.links[robot].update_pose(payload, stamp)
            elif topic == "battery":
                self.links[robot].update_battery(payload, stamp)
            elif topic == "docked":
                self.links[robot].dock_topic(payload, stamp)
            elif topic in ("/patient/bed_exit", "/robot/pause", "/robot/resume", "resume_rejected", "hazards"):
                safety.append((topic, payload, robot, stamp))
            else:
                raise ValueError(f"unknown input {topic}")
        for robot, link in self.links.items():
            arrival = link.settle()  # a COMPLETE STATE with no ARRIVED in this batch (4K)
            if arrival:
                messages.append((robot, arrival))
        for robot, link in self.links.items():
            online = link.online(now)
            if online != self.link_ok[robot]:
                self.resources.record("LINK_BACK" if online else "LINK_LOST", now, "로봇 상태 수신 재개" if online else "로봇 상태 수신 중단", robot=robot)
            self.link_ok[robot] = online
        return ui, safety, messages, results

    def tick(self, now):
        if now < self._last_tick:
            raise ValueError("tick time must be nondecreasing")
        self._last_tick = now
        out = FleetOutput()
        active = [robot for robot, unit in self.mission.robots.items() if unit.job]
        with self._lock:
            ui, safety, messages, results = self._drain(now, out)
            for topic, payload, _, stamp in safety:
                if topic == "/patient/bed_exit" and type(payload) is bool:
                    self.safety.observe(payload, active, stamp)
            self.safety.monitor.tick(now)
        for robot in active:
            self.safety.protect(robot, now)
        for topic, payload, robot, _ in safety:
            if topic == "hazards":
                self.safety.hazards(robot, payload, now, {r: link.pose for r, link in self.links.items()})
            elif topic == "/ui/operator_cmd":
                self._ui(topic, payload, now, out)
            elif topic == "/robot/pause":
                for r in self.links:
                    self.safety.pause(r, "MANUAL", now, by="ROS 서비스")
            elif topic == "/robot/resume":
                try:
                    self.safety.resume(self.links, now, individual=False, by="ROS 서비스")
                except ValueError:
                    pass  # rejected by safety; the event includes the reason
            elif topic == "resume_rejected":
                self.resources.record("RESUME_REJECTED", now, payload, by="ROS 서비스")
        self.collision.observe_distance(now)
        terminal = {r for r, msg in messages if msg["event"] in {"MISSION_FAILED", "MISSION_CANCELED", "ARRIVED", "DOCKED", "MOVE_REJECTED"}
                    or (msg["event"] == "CANCEL_CONFIRMED" and self.mission.robots[r].canceling)}
        accepted = {r for r, msg in messages if msg["event"] == "MISSION_ACCEPTED"}
        inspect = self.config.obstacle.mode == "inspect"
        for robot in self.links:
            self._stall_tick(robot, now, robot in terminal, robot in accepted, results[robot] if inspect else [], out)

        for robot, message in messages:
            self.mission.received(robot, message, now)
            if message["event"] == "OBSTACLE":
                self.reports.report(robot, message, now)
        for robot in self.links:
            if not inspect:
                self.reports.results(robot, results[robot], now)
            self.reports.tick(robot, now)
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
                                         can_yield=r.state != "FAULT" and not r.canceling and not r.replacing,
                                         stalled=self._stalled(robot),
                                         nav_paused=link.state in {"PAUSED", "STOPPING_PAUSE", "FAILED"},
                                         departed=bool(link.move and link.move.attempts),
                                         via=link.move.via if link.move else None,
                                         via_passed=bool(link.move and link.move.via_passed),
                                         proximity_stopped=self.collision.yielding_priority(robot))
        self.traffic.observe(now, reports, self.mission.robots)
        for data in clears:
            self._ui("/ui/operator_cmd", data, now, out)
        if self.mission.ready:
            # A leg finished by an arrival or cancel in this batch is requested again before planning.
            self.mission.plan(now)
        self.traffic.reconcile(now)
        arbitration = self.traffic.arbitrate(now)
        for leg in arbitration.grants:
            self.mission.granted(leg)
        for robot in arbitration.retarget:
            self.mission.retarget(robot, now)
        for robot, zone, slot in arbitration.arrived:
            # The robot already stands in its goal slot; the leg ends here without a MOVE.
            self.mission.received(robot, {"event": "ARRIVED", "destination": zone, "slot": slot}, now)
        self.collision.cards(now, self.mission.robots)
        for data in choices:
            self._ui("/ui/decision", data, now, out)

        self.traffic.reconcile(now)
        self.collision.arm_retreat(self.mission, now)
        motions = {request.robot: request.command for request in arbitration.commands}
        for robot in self.links:
            # Check the physical start again for every outgoing MOVE, including retries whose
            # first command may have been lost and grants issued to both robots in this tick.
            self.traffic.before_wire_move(robot, now)
            traffic_stop = self.traffic.traffic_stop(robot)
            command = self.mission.wire_step(robot, now, motions.get(robot), traffic_stop=traffic_stop,
                                             stalled=robot in self.blocks or self.stalls[robot].short,
                                             hold_restart=self.collision.hold_restart,
                                             hold_start=not self.traffic.may_start(robot))
            if command:
                if command.startswith("MOVE ") or command == "DOCK":
                    self.collision.access.sent(robot, now)
                out.commands.append(MotionRequest(robot, command))

        out.events = list(self.resources.events)
        out.cards = [e["detail"]["card"] for e in out.events if e["type"] == "CARD_OPEN"]
        self.resources.events.clear()
        self._unsaved_events.extend(out.events)
        for job in self.mission.dirty.values():
            if job.finished_at is not None and job.kind in schemas.MISSION_TYPES:
                self.history[job.id] = self.mission_row(job)
        while len(self.history) > self.config.publish.finished_mission_limit:
            self.history.pop(next(iter(self.history)))
        try:
            self.store.save(self.mission.dirty.values(), self._unsaved_events)
        except sqlite3.OperationalError as exc:
            # Deliver already decided safety commands; retry the atomic journal on the next tick.
            self._pending_acks.extend(out.acks)
            out.acks.clear()
            out.storage_error = str(exc)
        else:
            self.mission.dirty.clear()
            out.acks[:0] = self._pending_acks
            self._pending_acks.clear()
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
        if base != "FAULT" and r.returning:
            base = "RETURNING"
        if base != "FAULT" and (robot in self.collision.contacts or robot in self.collision.access.waits):
            base = "HOLD"
        return self.resources.robot_state(robot, base) if self.link_ok[robot] else "OFFLINE"

    def wait_reason(self, robot):
        wait = self.collision.near.get(robot) or self.collision.access.waits.get(robot)
        if wait:
            return wait.reason
        return self.resources.wait_reasons.get(robot)

    def mission_row(self, job):
        reason = self.wait_reason(job.robot) if job.status == "ACTIVE" else None
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
            leg_info = {"from": r.zone, "to": leg.destination} if leg else None
            slot = self.resources.slot_of(leg) if leg and not leg.dock_path else None
            if leg and not leg.dock_path:
                leg_info["slot"] = slot
            if leg and leg.dock_path:
                leg_info["purpose"] = "도크 복귀"
            if leg and r.target_override:
                leg_info["purpose"] = "이전 구역 복귀"
            causes = self.safety.causes[robot]
            access = self.collision.access.waits.get(robot)
            near = self.collision.near.get(robot)
            head_on = bool(near and frozenset((robot, near.peer)) in self.collision.head_on)
            route = []
            move = link.move if leg and leg is self.resources.active.get(robot) else None
            # NAVIGATING without "via" (PART 04 before 4J) drives straight through the door.
            via_done = bool(move and (move.via_passed or move.via is False))
            if leg and link.pose is not None:
                # The way the plan's graph walks: through the middle of a B·E door (4O).
                route = [link.pose, *door_route(self.config, link.pose, leg.destination, slot, via_done=via_done)]
                if leg.dock_path:
                    route[-1:] = leg.dock_path
            origin = self.resources._origins.get(robot) or region
            crossing = bool(leg and not leg.dock_path and (
                move.via if move and move.via is not None
                else origin and uses_crossroad(origin, leg.destination)))
            sighting = self.reports.yolo.get(robot)
            observation = self.stalls[robot].obstacle or self.reports.view(robot)
            robots[robot] = {
                "label": self.config.robots[robot].label, "mark": self.config.robots[robot].mark, "ns": link.ns, "link_ok": self.link_ok[robot],
                "route": [list(p) for p in route],
                "last_state_at": link.state_at if link.state_at != float("-inf") else None,
                "state_since": self.state_times.get(robot, (None, now))[1],
                "state": state, "part4_state": link.state, "last_event": link.last_event,
                "region": region, "zone": r.zone if region == g.ZONE_REGIONS[r.zone] else None,
                "pose": None if link.pose is None else dict(zip(("x", "y"), link.pose)),
                "battery_pct": link.battery, "battery_status": link.battery_status, "docked": link.docked, "mission_id": r.job.journal_id if r.job else None,
                "leg": leg_info,
                "returning": r.returning,
                "slot": r.slot if region == g.ZONE_REGIONS[r.zone] else None,
                "via": {"point": "교차로", "passed": via_done} if crossing else None,
                "yolo": sighting if sighting and now < sighting["at"] + 30 else None,
                "zone_wait": {"peer": access.peer, "zone": access.zone, "rule": access.rule, "exit": False,
                              "reason": access.reason, "since": access.since} if access else None,
                "proximity": {"peer": near.peer, "head_on": head_on, "peer_first": not head_on and self.collision.can_go_first(near.peer, robot, self.mission, now),
                              "self_first": not head_on and self.collision.can_go_first(robot, near.peer, self.mission, now), "reason": near.reason, "since": near.since} if near else None,
                "wait_reason": self.wait_reason(robot), "progress_pct": link.percent,
                "obstacle": observation,
                "obstacle_pending": (self.stalls[robot].requested_at is not None and self.stalls[robot].obstacle is None)
                                    or self.reports.pending(robot),
                "remaining_m": link.remaining_m, "stall_s": self.stalls[robot].seconds,
                "pause_causes": sorted(causes),
                "paused_by": sorted(causes)[0] if causes else None,
            }
            jobs = ([r.job] if r.job else []) + list(r.queue)
            missions.extend(self.mission_row(j) for j in jobs if j.kind in schemas.MISSION_TYPES)
        cards = list(self.decisions.cards)
        return {"v": 1, "stamp": now, "robots": robots, "resources": self.resources.snapshot(),
                "traffic": self.traffic.traffic_snapshot(),
                "missions": missions + list(reversed(self.history.values())), "open_cards": cards,
                "bed_exit": {"value": self.safety.bed_exit, "since": self.safety.since,
                             "monitor": self.safety.monitor.snapshot(now)},
                "kpi": self.store.stats.kpi()}
