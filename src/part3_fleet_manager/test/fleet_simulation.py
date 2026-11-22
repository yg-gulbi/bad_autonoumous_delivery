"""Drive the real fleet tick with simulated ROS inputs and validate every wire payload."""
import json
from dataclasses import replace

from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.fleet_core import FleetCore


class Simulation:
    """Scripted PART 04 frames around the real FleetCore tick.

    ``settle_plan`` (default): while the plan is due or being made (plan_runner), the next ticks come right away at
    the same time (at most three) and the outputs are returned as one. The plan's result is used one tick later (plan_runner);
    these tests are about missions, safety, cards and the protocol, not about when a plan comes in (the kinematic
    mock tests keep every tick)."""

    def __init__(self, cfg, *, require_at_start=False, slots=False, settle_plan=True):
        self.settle_plan = settle_plan
        # The simulator normally runs without a webcam, just like fleet_mock.launch.py.
        monitor = replace(cfg.safety.bed_monitor, require_at_start=require_at_start)
        cfg = replace(cfg, safety=replace(cfg.safety, bed_monitor=monitor))
        self.cfg = cfg
        self.core = FleetCore(cfg)
        self.pending = []
        self.seq = 0
        self.frames = {r: {"type": "STATE", "robot_id": c.ns, "state": "IDLE", "mission_id": None,
                           "pose": self.pose(c.start_zone), "percent": 0}
                       for r, c in cfg.robots.items()}
        if slots:  # 4J PART 04: every STATE carries "slot" (null while idle)
            for frame in self.frames.values():
                frame["slot"] = None
        self.ids = {}
        self.last = {}
        self.now = 0

    def pose(self, zone):
        xy = self.cfg.zones[zone] if isinstance(zone, str) else zone
        return dict(zip(("x", "y"), xy))

    def tick(self, now):
        self.now = now
        for r, frame in self.frames.items():
            self.core.enqueue("status", json.dumps(frame), now, robot=r)
        for topic, payload, robot in self.pending:
            self.core.enqueue(topic, payload, now, robot=robot)
        self.pending.clear()
        out = self.core.tick(now)
        pm = self.core.resources
        for _ in range(3):
            if not self.settle_plan or not (pm.runner.busy or pm.dirty is not None):
                break
            more = self.core.tick(now)
            for name in ("inspections", "commands", "events", "cards", "acks"):
                getattr(out, name).extend(getattr(more, name))
            out.state = more.state or out.state
            out.storage_error = more.storage_error or out.storage_error
        for c in out.commands:
            if c.command.startswith("MOVE "):
                self.last[c.robot] = c.command
        for ack in out.acks:
            schemas.parse_ack(schemas.dumps(ack))
            if ack["mission_id"]:
                self.ids[ack["ref"]] = ack["mission_id"]
        if out.state:
            schemas.parse_fleet_state(schemas.dumps(out.state))
        for card in out.cards:
            schemas.parse_decision_req(schemas.dumps(card))
        for e in out.events:
            schemas.parse_event(schemas.dumps(e))
        return out

    def patient(self, ref="p", pickup="A", dest="C", level=2):
        self.pending.append(("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "PATIENT_TRANSPORT", "pickup": pickup, "destination": dest,
            "level": level, "patient": {"name": "테스트", "chart_no": "TEST"}, "requested_by": "nurse"}), None))

    def call(self, ref, zone="A", level=1):
        self.pending.append(("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "CALL", "level": level, "destination": zone,
            "requested_by": "nurse"}), None))

    def battery(self, robot, pct):
        self.pending.append(("battery", pct, robot))

    def med(self, ref="m", stops=("F", "E", "B"), level=3):
        self.pending.append(("/ui/mission_req", json.dumps({
            "v": 1, "req_id": ref, "type": "MED_ROUND", "level": level,
            "stops": [{"zone": z, "item": "테스트 물품"} for z in stops], "requested_by": "nurse"}), None))

    def cmd(self, name, **fields):
        self.seq += 1
        self.pending.append(("/ui/operator_cmd", json.dumps({
            "v": 1, "cmd": name, "cmd_id": f"c-{self.seq}", "by": "nurse", **fields}), None))

    def choose(self, card, choice):
        self.pending.append(("/ui/decision", json.dumps({"v": 1, "card_id": card["card_id"],
                                                        "by": "nurse", "choice": choice}), None))

    def event(self, robot, name, **fields):
        f = self.frames[robot]
        if name == "MISSION_ACCEPTED":
            words = self.last[robot].split()
            room = words[1]  # "F" or "F2" (slot 2)
            self.seq += 1
            tag = words[3][3:] if len(words) > 3 else f"remote-{self.seq}"
            slot = int(room[1]) if len(room) == 2 else 1
            f.update(mission_id=tag, destination=room[0], state="MOVING", percent=0)
            if "slot" in f:
                f["slot"] = slot
                fields.update(slot=slot)
            fields.update(destination=room[0])
        if name == "ARRIVED":
            slot = f.get("slot") or 1
            fields.update(destination=f["destination"], pose=self.pose(self.cfg.slot_point(f["destination"], slot)))
            if "slot" in f:
                fields.update(slot=slot)
            f.update(state="COMPLETE", pose=fields["pose"], percent=100)
        if name in ("PAUSED", "RESUMED", "MISSION_FAILED", "MISSION_CANCELED"):
            f["state"] = {"PAUSED": "PAUSED", "RESUMED": "MOVING", "MISSION_FAILED": "FAILED",
                          "MISSION_CANCELED": "CANCELED"}[name]
        self.pending.append(("status", json.dumps({"type": "STATUS", "event": name, "robot_id": f["robot_id"],
                                                  "mission_id": f["mission_id"], **fields}), robot))

    def confirm(self, r, ref, result="OK"):
        self.cmd("CONFIRM_STEP", robot=r, mission_id=self.ids[ref], result=result)

    def state(self, robot):
        return self.core.snapshot(self.now)["robots"][robot]["state"]
