"""Stage 4O follow-up: the fixes the new random events (docking, long link loss, double cancel) brought out.

- A robot cancelled before it left its dock stays home (PART 04 ignores DOCK while docked).
- PLAN: a dock leg goes to its aside slot first, like any leg (no CANCEL/DOCK loop).
- PLAN: an arrival at an aside slot is judged from the MOVE that was sent (it may come back only with the
  first STATE after a link loss, when the robot has no plan).
- PLAN: a robot with a MOVE but no plan counts only for the slot it left or goes to.
- PLAN: a robot that loses the link while driving also holds the free segments on the rest of its MOVE (UNKNOWN);
  after the operator's check it keeps the segment it is in.
"""
from dataclasses import replace
import json

from kinematic_mock import KinematicSim
from part3_fleet_manager.core.plan_traffic import PlanLeg, RobotPlan
from part3_fleet_manager.core.legs import Leg
from part3_fleet_manager.core.robot_protocol import DONE

A, B = "robot_a", "robot_b"


def dock_config(cfg):
    return replace(cfg, robots={**cfg.robots, B: replace(cfg.robots[B], return_to="DOCK")})


def test_cancel_while_undocking_keeps_the_robot_home_on_its_dock(cfg):
    sim = KinematicSim(dock_config(cfg), docked={B})
    try:
        ref = sim.submit_call("E", level=2)
        while sim.now < 10 and sim.robots[B].state != "UNDOCKING":
            sim.step(0.2)
        assert sim.robots[B].state == "UNDOCKING"
        sim.cancel(sim.ids[ref])
        canceled_at = sim.now
        sim.run_until(sim.now + 30.0)
        bot, unit = sim.robots[B], sim.core.mission.robots[B]
        assert bot.docked and bot.state in DONE
        assert unit.state == "IDLE" and unit.job is None
        assert not [c for t, r, c in sim.all_commands if r == B and t > canceled_at and c == "DOCK"]
        assert not any(e["type"] in ("FAULT", "MOVE_REJECTED") for e in sim.all_events)
    finally:
        sim.close()


def test_plan_dock_leg_goes_to_its_aside_slot_first(cfg):
    sim = KinematicSim(dock_config(cfg), docked={B})
    try:
        pm = sim.core.resources
        unit = cfg.robots[B]
        leg = Leg(B, "F", 1, 0.0, None, False, "RETURN_HOME", dock_path=unit.dock.path_end())
        assert pm.move_target(leg) == ("DOCK", None)                       # no plan yet
        pm.plans[B] = RobotPlan([PlanLeg(0, "B1", True, (), 0.0), PlanLeg(1, "DOCK_B", False, (), 0.0)], [])
        assert pm.move_target(leg) == ("B", 1)                             # make way first
        pm.plans[B] = RobotPlan([PlanLeg(1, "DOCK_B", False, (), 0.0)], [])
        assert pm.move_target(leg) == ("DOCK", None)
    finally:
        sim.close()


def test_plan_aside_arrival_without_a_plan_after_a_link_loss(cfg):
    sim = KinematicSim(cfg)
    try:
        pm, link = sim.core.resources, sim.core.links[B]
        leg = Leg(B, "F", 1, sim.now, "M-0001", False, "MED_ROUND")
        pm.active[B] = leg
        pm.plans.pop(B, None)
        link.go("L-0001-1", "A", 1, slot=2)                                  # the plan had sent it aside to A2
        assert pm.arrived_aside(B, {"destination": "A", "slot": 2}, sim.now)
        assert pm.pending[B] is leg and B not in pm.active
        assert pm.events[-1]["type"] == "PLAN_YIELD_MOVE" and pm.events[-1]["detail"]["target"] == "A2"
        pm.active[B] = pm.pending.pop(B)
        link.go("L-0001-2", "F", 1, slot=2)                                  # a goal slot is not an aside
        assert not pm.arrived_aside(B, {"destination": "F", "slot": 2}, sim.now)
    finally:
        sim.close()


def test_plan_slot_member_without_a_plan_is_only_the_move_ends(cfg):
    sim = KinematicSim(cfg)
    try:
        pm, link = sim.core.resources, sim.core.links[B]
        link.go("L-0001-1", "D", 1, slot=2)
        link.move.attempts = 1                                               # sent, not answered yet
        pm.plans.pop(B, None)
        pm.departed_from[B] = "E1"
        assert pm.robots.view(B).driving
        assert pm._slot_member(B, "D2") and pm._slot_member(B, "E1")
        assert not pm._slot_member(B, "A2")
    finally:
        sim.close()


def test_plan_link_lost_while_driving_holds_the_rest_of_the_move(cfg):
    sim = KinematicSim(cfg)
    try:
        sim.cmd("GO_ZONE", robot=A, zone="C", level=2)
        while sim.now < 30 and not (sim.robots[A].state == "MOVING" and sim.robots[A].pose[1] < -0.8):
            sim.step(0.2)
        sim.drop_link(A, 6.0)
        board = sim.core.resources.segments
        claimed = set()
        while sim.now < sim.robots[A].drop_until - 0.2:
            sim.step(0.2)
            if not sim.core.link_ok[A]:
                claimed |= {n for n, s in board.items() if A in s.holders and A in s.uncertain}
        # Offline while driving down: the crossroad and the bottom corridor on the rest of its MOVE are held.
        assert {"CROSSROAD", "CORRIDOR_BOTTOM"} <= claimed
        # Back online: the operator checks the UNKNOWN segments; A keeps the one it is in until it leaves.
        end = sim.now + 60.0
        while sim.now < end and sim.core.mission.robots[A].job is not None:
            for name, seg in board.items():
                if seg.state == "UNKNOWN" and all(sim.core.link_ok[r] for r in seg.uncertain):
                    sim.cmd("RESOURCE_CLEAR_CONFIRM", resource=name)
            sim.step(0.2)
            for name, seg in board.items():
                if name in sim.core.resources.map.narrow_names and seg.spec.contains(sim.robots[A].pose):
                    assert A in seg.holders, (sim.now, name)
        assert sim.core.mission.robots[A].zone == "C"
    finally:
        sim.close()


def test_cancel_of_a_mission_that_ended_meanwhile_is_confirmed_by_the_next_state(cfg):
    """A CANCEL for the running MOVE (here an aside move, while a new MOVE waits unsent) is lost in a link drop and
    that MOVE ends meanwhile. PART 04 ignores a CANCEL with nothing running, so the first STATE after the CANCEL
    showing that mission COMPLETE stands for the confirmation (before: CANCEL resent forever, missions stuck)."""
    sim = KinematicSim(cfg)
    try:
        link, ns = sim.core.links[A], cfg.robots[A].ns
        t = sim.now + 1.0
        link.go("Y-1-1", "D", 1, slot=2)
        assert link.step(t, stop=False, resume=False)[0].startswith("MOVE D2")
        link.receive(json.dumps({"type": "STATUS", "event": "MISSION_ACCEPTED", "robot_id": ns, "mission_id": "Y-1-1",
                                 "destination": "D", "slot": 2}), t + 0.1)
        assert link.move.remote_id == "Y-1-1"
        link.go("L-0001-1", "C", 1)                              # the next leg's MOVE, not sent yet
        link.cancel()                                            # replace: stop the aside move first
        assert link.step(t + 0.2, stop=False, resume=False)[0] == "CANCEL"
        state = {"type": "STATE", "robot_id": ns, "mission_id": "Y-1-1", "state": "COMPLETE", "destination": "D",
                 "slot": 2, "pose": {"x": -0.75, "y": -0.15}, "docked": False}
        assert link.receive(json.dumps(state), t + 0.1) is None          # older than the CANCEL: not an answer
        assert link.receive(json.dumps(state), t + 3.0) == {"event": "CANCEL_CONFIRMED"}
        assert not link.cancel_wait
    finally:
        sim.close()
