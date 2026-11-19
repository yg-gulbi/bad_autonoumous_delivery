from dataclasses import replace

import pytest

from part3_fleet_manager.core.deadlock import DeadlockManager
from part3_fleet_manager.core.resource_manager import MotionRequest, ResourceManager, RobotReport
from part3_fleet_manager.core.schemas import dumps, parse_event


A, B = "robot_a", "robot_b"


class Encounter:
    def __init__(self, cfg, origins=("A", "F"), destinations=("F", "A"), levels=(1, 3)):
        cfg = replace(cfg, corridor=replace(cfg.corridor, mode="SHARED_YIELD"))
        self.cfg = cfg
        self.rm = ResourceManager(cfg)
        self.reports = {r: RobotReport(cfg.zones[z], True) for r, z in zip((A, B), origins)}
        self.legs = [self.rm.request(r, dest, level, 0) for r, dest, level in zip((A, B), destinations, levels)]
        result = self.rm.tick(1, self.reports)
        assert set(result.grants) == set(self.legs)
        assert result.commands == []

    def tick(self, now, a=None, b=None):
        for robot, pose in ((A, a), (B, b)):
            if pose is not None:
                self.reports[robot] = RobotReport(pose, True)
        return self.rm.tick(now, self.reports).commands


def test_21_head_on_pauses_only_lower_level_then_resumes_after_passing(cfg):
    scene = Encounter(cfg)
    rm = scene.rm
    assert scene.tick(2, (-1.55, -2.0), (-1.55, -3.0)) == [MotionRequest(A, "PAUSE")]
    assert rm.robot_state(A, "MOVING") == "GIVE_WAY" and rm.robot_state(B, "MOVING") == "MOVING"
    assert "Robot B Level 3" in rm.wait_reasons[A]
    snapshot = rm.snapshot()["CORRIDOR"]
    assert snapshot["state"] == "OCCUPIED" and snapshot["holders"] == [B, A]
    assert snapshot["give_way"] == {"yielding": A, "to": B, "since": 2}
    assert scene.tick(3, b=(-1.55, -2.1)) == []
    assert scene.tick(4, b=(-1.55, -1.8)) == []  # passed, still too close
    assert scene.tick(5, b=(-1.55, -1.5)) == [MotionRequest(A, "RESUME")]
    assert rm.robot_state(A, "MOVING") == "MOVING" and A not in rm.wait_reasons
    assert scene.tick(6) == [] and rm.snapshot()["CORRIDOR"]["give_way"] is None
    events = [e for e in rm.events if e["type"].startswith("CORRIDOR_YIELD_")]
    assert [e["type"] for e in events] == ["CORRIDOR_YIELD_START", "CORRIDOR_YIELD_END"]
    assert events[0]["detail"]["rule"] == "LEVEL"
    assert all(parse_event(dumps(e)) == e for e in events)


@pytest.mark.parametrize(("first", "expected"), [(A, B), (B, A), (None, B)])
def test_22_equal_level_uses_entry_time_then_robot_b(cfg, first, expected):
    scene = Encounter(cfg, levels=(2, 2))
    if first == A:
        assert not scene.tick(2, a=(-1.55, -2))
    if first == B:
        assert not scene.tick(2, b=(-1.55, -3))
    assert scene.tick(3, (-1.55, -2), (-1.55, -3)) == [MotionRequest(expected, "PAUSE")]
    assert scene.rm.last_decision["detail"]["rule"] == ("ENTRY_ORDER" if first else "ROBOT_ID")


@pytest.mark.parametrize(("origins", "destinations", "a", "b"), [
    (("A", "D"), ("C", "F"), (-1.55, -2), (-1.55, -3)),  # following
    (("B", "F"), ("E", "D"), (-1.55, -1.8), (-1.55, -2.6)),  # lateral room crossing
    (("A", "F"), ("C", "D"), (-1.55, -1.6), (-1.55, -3)),  # too far
    (("A", "F"), ("C", "D"), (-1.55, -3), (-1.55, -2)),  # diverging
])
def test_23_no_yield(cfg, origins, destinations, a, b):
    scene = Encounter(cfg, origins, destinations)
    assert scene.tick(2, a, b) == []
    assert scene.rm.snapshot()["CORRIDOR"]["give_way"] is None


def test_room_door_direction_uses_current_pose(cfg):
    scene = Encounter(cfg, destinations=("E", "D"))
    assert scene.tick(2, (-1.55, -1.65), (-1.55, -2.55)) == [MotionRequest(A, "PAUSE")]


def test_24_no_progress_timeout_applies_once_per_encounter(cfg):
    scene = Encounter(cfg)
    assert scene.tick(2, (-1.55, -2), (-1.55, -3)) == [MotionRequest(A, "PAUSE")]
    assert not scene.tick(11.99)
    assert scene.tick(12) == [MotionRequest(A, "RESUME")]
    assert scene.rm.events[-1]["type"] == "CORRIDOR_YIELD_TIMEOUT"
    assert scene.rm.robot_state(A, "MOVING") == "MOVING"
    assert not scene.tick(12.1) and not scene.tick(23)


def test_priority_robot_progress_restarts_stall_timer(cfg):
    scene = Encounter(cfg)
    scene.tick(2, (-1.55, -2), (-1.55, -3))
    assert not scene.tick(8, b=(-1.55, -2.8))
    assert not scene.tick(12) and not scene.tick(17.99)
    assert scene.tick(18) == [MotionRequest(A, "RESUME")]


def test_priority_robot_exits_before_reaching_release_distance(cfg):
    scene = Encounter(cfg, destinations=("C", "E"))
    # Robot B approaches the room mouth from below.
    assert scene.tick(2, (-1.54, -2.15), (-1.54, -2.75)) == [MotionRequest(A, "PAUSE")]
    assert scene.tick(3, b=(-0.95, -2.4)) == [MotionRequest(A, "RESUME")]
    assert scene.rm.resources["CORRIDOR"].holders[B].state == "OCCUPIED"  # clearance timer still running


def test_yield_disabled_has_two_slots(cfg):
    scene = Encounter(replace(cfg, corridor=replace(cfg.corridor, yield_enabled=False)))
    assert not scene.tick(2, (-1.55, -2), (-1.55, -3))
    assert scene.rm.resources["CORRIDOR"].state == "OCCUPIED"


def test_safe_pause_takes_over_without_automatic_resume(cfg):
    scene = Encounter(cfg)
    scene.tick(2, (-1.55, -2), (-1.55, -3))
    scene.reports[A] = replace(scene.reports[A], safe_pause=True)
    assert not scene.tick(3)
    assert scene.rm.robot_state(A, "MOVING") == "SAFE_PAUSE"
    assert not scene.tick(14)
    scene.reports[A] = replace(scene.reports[A], safe_pause=False)
    assert scene.tick(15) == [MotionRequest(A, "PAUSE")]


def test_shared_holder_clearance_and_unknown_are_individual(cfg):
    scene = Encounter(cfg)
    rm = scene.rm
    scene.tick(2, (-1.55, -1.6), (-1.55, -3.2))
    scene.reports[A] = RobotReport(cfg.zones["F"], False)
    assert not scene.tick(3)
    assert rm.resources["CORRIDOR"].state == "UNKNOWN"
    scene.tick(9)
    assert rm.snapshot()["CORRIDOR"]["holders"] == [B, A]
    rm.clear_unknown("CORRIDOR", "nurse", 9)
    assert rm.snapshot()["CORRIDOR"]["holders"] == [B]
    assert rm.resources["CORRIDOR"].state == "OCCUPIED"
    scene.tick(10, b=cfg.zones["D"])
    assert rm.resources["CORRIDOR"].holder == B
    scene.tick(11)
    assert rm.resources["CORRIDOR"].state == "FREE"


def test_shared_graph_waits_for_all_holders_only_when_full(cfg):
    c = "robot_c"
    fleet = replace(cfg, corridor=replace(cfg.corridor, mode="SHARED_YIELD"), robots={**cfg.robots, c: replace(cfg.robots[A], ns="robot4")})
    rm = ResourceManager(fleet)
    dl = DeadlockManager(rm)
    reports = {A: RobotReport((-1.55, -1.7), True), B: RobotReport(cfg.zones["D"], True),
               c: RobotReport(cfg.zones["F"], True)}
    rm.request(c, "D", 1, 0)
    rm.tick(0, reports)
    assert dl.wait_graph().number_of_edges() == 0
    reports[B] = RobotReport((-1.55, -3.1), True)
    assert rm.tick(1, reports).grants == []
    assert set(dl.wait_graph().edges) == {(c, A), (c, B)}
    reports[c] = RobotReport((-1.55, -2.3), True)
    rm.tick(2, reports)
    assert rm.resources["CORRIDOR"].state == "UNKNOWN"


def test_confirming_unexpected_occupant_preserves_live_grants(cfg):
    scene = Encounter(cfg)
    rm = scene.rm
    scene.reports["robot_c"] = RobotReport((-1.5, -2.1), True)
    scene.tick(2)
    corridor = rm.resources["CORRIDOR"]
    assert corridor.state == "UNKNOWN"
    with pytest.raises(ValueError, match="E-423"):
        rm.clear_unknown("CORRIDOR", "nurse", 2)
    scene.reports["robot_c"] = RobotReport(cfg.zones["D"], True)
    scene.tick(3)
    rm.clear_unknown("CORRIDOR", "nurse", 3)
    assert list(corridor.holders) == [B, A] and corridor.state == "GRANTED"


def test_shared_corridor_mixed_claims_and_independent_release(cfg):
    scene = Encounter(cfg)
    rm = scene.rm
    scene.tick(2, a=(-1.55, -2))
    corridor = rm.resources["CORRIDOR"]
    assert corridor.state == "OCCUPIED"
    assert {r: h.state for r, h in corridor.holders.items()} == {B: "GRANTED", A: "OCCUPIED"}
    scene.tick(3, a=cfg.zones["F"])
    scene.tick(3.99)
    assert corridor.state == "OCCUPIED"
    scene.tick(4)
    assert list(corridor.holders) == [B] and corridor.state == "GRANTED"


def test_granted_priority_robot_approaching_mouth_has_not_yet_exited(cfg):
    scene = Encounter(cfg, levels=(3, 1))
    # A approaches the top mouth with a grant while B is already inside.
    assert scene.tick(2, a=(-1.4, -0.48), b=(-1.45, -1.3)) == [MotionRequest(B, "PAUSE")]
    assert scene.rm.resources["CORRIDOR"].holders[A].state == "GRANTED"
    assert not scene.tick(3)
