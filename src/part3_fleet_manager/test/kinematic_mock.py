"""Pure-Python kinematic mock for PART 04 robots (Stage 4O 7B/7C)."""
from dataclasses import replace
from itertools import pairwise
import json
from math import dist

from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.fleet_core import FleetCore
from part3_fleet_manager.core.robot_protocol import DONE
from part3_fleet_manager.core.plan_graph import door_route


def _parse_cmd(text: str):
    words = text.strip().split()
    tag = None
    if words and words[-1].lower().startswith("id="):
        tag = words.pop()[3:] or None
    words = [w.upper() for w in words]
    if len(words) == 1 and words[0] in ("PAUSE", "RESUME", "CANCEL", "DOCK", "UNDOCK"):
        return words[0], None, None, tag, None
    if words[:1] == ["MOVE"]:
        words = words[1:]
    room = words[0]
    slotted = len(room) == 2 and room[1] in "12"
    zone = room[0]
    slot = int(room[1]) if slotted else 1
    level = int(words[1]) if len(words) == 2 else 1
    return "MOVE", zone, level, tag, slot


class KinematicRobot:
    """Single robot moving at speed_mps and emitting 1 Hz STATE. It drives plan_graph.door_route: into and
    out of room B or E through the middle of the door and the point inside the room, as Nav2 would."""

    def __init__(self, cfg, robot_id: str, *, speed_mps: float = 0.2, start_pose=None):
        self.cfg = cfg
        self.robot_id = robot_id
        unit = cfg.robots[robot_id]
        self.ns = unit.ns
        self.speed_mps = speed_mps
        self.pose: tuple[float, float] = (
            cfg.zones[start_pose] if isinstance(start_pose, str)
            else tuple(start_pose) if start_pose is not None
            else cfg.zones[unit.start_zone]
        )
        self.state = "IDLE"
        self.mid: str | None = None
        self.dest: str | None = None
        self.slot: int | None = None
        self.level = 1
        self.reason = ""
        self.path: list[tuple[float, float]] = []
        self.total = 0.0
        self.via: tuple[float, float] | None = None
        self.drop_until = -1.0
        self.next_state_at = 0.0
        self.battery_pct = 80.0 if robot_id == "robot_a" else 75.0
        self._outbox: list[tuple[str, object]] = []

    def _emit_status(self, event: str, **fields):
        payload = json.dumps({
            "type": "STATUS",
            "event": event,
            "robot_id": self.ns,
            "mission_id": self.mid,
            **fields,
        })
        self._outbox.append(("status", payload))

    def _emit_state(self):
        rem = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path])) if self.state == "MOVING" else None
        pct = round(max(0.0, 100.0 * (1.0 - rem / self.total)), 1) if (rem is not None and self.total) else (100.0 if self.state == "COMPLETE" else 0.0)
        payload = json.dumps({
            "type": "STATE",
            "robot_id": self.ns,
            "mission_id": self.mid,
            "state": self.state,
            "destination": self.dest,
            "slot": self.slot,
            "priority": self.level,
            "reason": self.reason,
            "pose": {"x": round(self.pose[0], 3), "y": round(self.pose[1], 3)},
            "docked": False,
            "ready": True,
            "battery": {"percent": self.battery_pct, "voltage": 15.0, "status": "DISCHARGING"},
            "remaining_m": round(rem, 2) if rem is not None else None,
            "percent": pct,
        })
        self._outbox.append(("status", payload))

    def drop_link(self, now: float, seconds: float):
        self.drop_until = max(self.drop_until, now + seconds)

    def receive(self, text: str, now: float):
        action, dest, level, tag, slot = _parse_cmd(text)
        if action == "MOVE" and self.state in DONE:
            self.dest = dest
            self.level = level or 1
            self.mid = tag or f"sim-{int(now * 1000)}"
            self.slot = slot or 1
            self.reason = ""
            self.path = door_route(self.cfg, self.pose, dest, self.slot)
            self.via = self.cfg.corridor.crossroad if self.cfg.corridor.crossroad in self.path[:-1] else None
            self.total = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path]))
            self.state = "MOVING"
            self._emit_status("MISSION_ACCEPTED", destination=self.dest, priority=self.level, slot=self.slot)
            self._emit_status("NAVIGATING", destination=self.dest, via="교차로" if self.via else None)
        elif action == "PAUSE" and self.state == "MOVING":
            self.reason = "관제 요청"
            self.state = "STOPPING_PAUSE"
        elif action == "RESUME" and self.state == "PAUSED":
            self.state = "MOVING"
            self._emit_status("RESUMED")
            self._emit_status("NAVIGATING", destination=self.dest, via="교차로" if self.via else None)
        elif action == "CANCEL" and self.state not in DONE:
            self.reason = "운영자 취소"
            self.state = "STOPPING_CANCELED"

    def step(self, now: float, dt: float) -> list[tuple[str, object]]:
        if self.state == "STOPPING_PAUSE":
            self.state = "PAUSED"
            self._emit_status("PAUSED", reason=self.reason)
        elif self.state == "STOPPING_CANCELED":
            self.state = "CANCELED"
            self.path.clear()
            self.via = None
            self._emit_status("MISSION_CANCELED", reason=self.reason)
        elif self.state == "MOVING":
            budget = self.speed_mps * dt
            moved = False
            while self.path and budget > 0:
                goal = self.path[0]
                length = dist(self.pose, goal)
                if length <= 1e-9:
                    self.path.pop(0)
                    if self.via is not None and goal == self.via:
                        self.via = None
                        self._emit_status("VIA_PASSED", destination=self.dest)
                        self._emit_status("NAVIGATING", destination=self.dest, via=None)
                    continue
                step_d = min(length, budget)
                if step_d >= length - 1e-9:
                    self.pose = goal
                    budget -= length
                    self.path.pop(0)
                    moved = True
                    if self.via is not None and goal == self.via:
                        self.via = None
                        self._emit_status("VIA_PASSED", destination=self.dest)
                        self._emit_status("NAVIGATING", destination=self.dest, via=None)
                else:
                    ratio = step_d / length
                    self.pose = (
                        self.pose[0] + (goal[0] - self.pose[0]) * ratio,
                        self.pose[1] + (goal[1] - self.pose[1]) * ratio,
                    )
                    budget = 0.0
                    moved = True
            if moved and now >= self.drop_until:
                self._outbox.append(("pose", {"x": round(self.pose[0], 4), "y": round(self.pose[1], 4)}))
            if not self.path:
                self.state = "COMPLETE"
                self._emit_status(
                    "ARRIVED",
                    destination=self.dest,
                    slot=self.slot,
                    pose={"x": round(self.pose[0], 3), "y": round(self.pose[1], 3)},
                    percent=100.0,
                    goal_error_m=0.0,
                )

        if now >= self.next_state_at - 1e-9:
            self.next_state_at = now + 1.0
            if now >= self.drop_until:
                self._emit_state()
        elif now >= self.drop_until and self.drop_until >= 0.0:
            # Link just recovered — keep normal 1 Hz schedule, pose already resumes immediately.
            pass

        if now < self.drop_until:
            # Drop outgoing telemetry while link is disconnected (commands may also be missed if offline).
            self._outbox.clear()
            return []

        msgs = list(self._outbox)
        self._outbox.clear()
        return msgs


class KinematicSim:
    """Drives FleetCore with pure-Python KinematicRobots at 0.2 m/s."""

    def __init__(self, cfg, *, mode: str = "PLAN", speed_mps: float = 0.2, poses: dict | None = None,
                 require_at_start: bool = False):
        monitor = replace(cfg.safety.bed_monitor, require_at_start=require_at_start)
        cfg = replace(
            cfg,
            db_path=":memory:",
            safety=replace(cfg.safety, bed_monitor=monitor),
            traffic=replace(cfg.traffic, mode=mode),
        )
        self.cfg = cfg
        self.core = FleetCore(cfg)
        self.robots = {
            r: KinematicRobot(cfg, r, speed_mps=speed_mps, start_pose=(poses or {}).get(r))
            for r in cfg.robots
        }
        self.now = 0.0
        self.seq = 0
        self.ids: dict[str, str] = {}
        self.all_events: list[dict] = []
        self.all_commands: list[tuple[float, str, str]] = []
        # Prime initial STATE + battery at t=0.
        for r, bot in self.robots.items():
            bot._emit_state()
            for topic, payload in bot._outbox:
                self.core.enqueue(topic, payload, 0.0, robot=r)
            bot._outbox.clear()
            bot.next_state_at = 1.0
            self.core.enqueue("battery", bot.battery_pct, 0.0, robot=r)
        out = self.core.tick(0.0)
        self.all_events.extend(out.events)

    def close(self):
        self.core.store.close()

    def _next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.seq}"

    def submit_patient(self, pickup: str, dest: str, level: int = 2, ref: str | None = None) -> str:
        ref = ref or self._next_id("pat")
        self.core.enqueue("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "PATIENT_TRANSPORT", "pickup": pickup, "destination": dest,
            "level": level, "patient": {"name": "환자", "chart_no": f"C-{self.seq}"}, "requested_by": "nurse",
        }), self.now)
        return ref

    def submit_med(self, stops: tuple[str, ...] | list[str], level: int = 3, ref: str | None = None) -> str:
        ref = ref or self._next_id("med")
        self.core.enqueue("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "MED_ROUND", "level": level,
            "stops": [{"zone": z, "item": "약품"} for z in stops], "requested_by": "nurse",
        }), self.now)
        return ref

    def submit_call(self, zone: str, level: int = 1, ref: str | None = None) -> str:
        ref = ref or self._next_id("call")
        self.core.enqueue("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "CALL", "level": level, "destination": zone, "requested_by": "nurse",
        }), self.now)
        return ref

    def cmd(self, name: str, **fields):
        cid = self._next_id("cmd")
        self.core.enqueue("/ui/operator_cmd", json.dumps({
            "v": 1, "cmd": name, "cmd_id": cid, "by": "nurse", **fields,
        }), self.now)
        return cid

    def choose(self, card_id: str, choice: str):
        self.core.enqueue("/ui/decision", json.dumps({
            "v": 1, "card_id": card_id, "choice": choice, "by": "nurse",
        }), self.now)

    def confirm(self, robot: str, mission_id: str | None = None):
        job = self.core.mission.robots[robot].job
        mid = mission_id or (job.id if job else None)
        if mid:
            self.cmd("CONFIRM_STEP", robot=robot, mission_id=mid, result="OK")

    def confirm_waiting(self):
        for r, unit in self.core.mission.robots.items():
            if unit.state == "WAIT_CONFIRM" and unit.job:
                self.confirm(r, unit.job.id)

    def cancel(self, mission_id: str):
        return self.cmd("CANCEL_MISSION", mission_id=mission_id)

    def pause(self, robot: str):
        return self.cmd("PAUSE_ROBOT", robot=robot)

    def resume(self, robot: str):
        return self.cmd("RESUME_ROBOT", robot=robot)

    def drop_link(self, robot: str, seconds: float):
        self.robots[robot].drop_link(self.now, seconds)

    def step(self, dt: float = 0.2, *, validate_wire: bool = False):
        self.now = round(self.now + dt, 6)
        for r, bot in self.robots.items():
            for topic, payload in bot.step(self.now, dt):
                self.core.enqueue(topic, payload, self.now, robot=r)
        out = self.core.tick(self.now)
        for cmd in out.commands:
            self.all_commands.append((self.now, cmd.robot, cmd.command))
            if self.now >= self.robots[cmd.robot].drop_until:
                self.robots[cmd.robot].receive(cmd.command, self.now)
        for ack in out.acks:
            if ack.get("mission_id"):
                self.ids[ack["ref"]] = ack["mission_id"]
            if validate_wire:
                schemas.parse_ack(schemas.dumps(ack))
        if validate_wire and out.state:
            schemas.parse_fleet_state(schemas.dumps(out.state))
        if validate_wire:
            for e in out.events:
                schemas.parse_event(schemas.dumps(e))
        self.all_events.extend(out.events)
        return out

    def run_until(self, end_t: float, *, dt: float = 0.2, auto_confirm: bool = True, on_tick=None):
        while self.now < end_t - 1e-9:
            if auto_confirm:
                self.confirm_waiting()
            out = self.step(dt)
            if on_tick:
                on_tick(self, out)
