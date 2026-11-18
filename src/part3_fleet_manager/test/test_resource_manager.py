from dataclasses import replace

import pytest

from part3_fleet_manager.core.resource_manager import ResourceManager, RobotReport
from part3_fleet_manager.core.schemas import dumps, parse_event


A, B = "robot_a", "robot_b"
MID = (-1.6, -2.0)


@pytest.fixture
def manager(cfg):
    return ResourceManager(replace(cfg, corridor=replace(cfg.corridor, mode="EXCLUSIVE")))


@pytest.fixture
def reports(cfg):
    return {A: RobotReport(cfg.zones["D"], True), B: RobotReport(cfg.zones["F"], True)}


@pytest.mark.parametrize(("levels", "since", "winner"), [
    ((1, 3), (0, 0), B), ((3, 1), (0, 0), A),
    ((2, 2), (0, 0.25), A), ((2, 2), (0, 0), A),
])
@pytest.mark.parametrize("mode", ["EXCLUSIVE", "SHARED_YIELD"])
def test_1_2_priority_and_fifo(cfg, reports, levels, since, winner, mode):
    manager = ResourceManager(replace(cfg, corridor=replace(cfg.corridor, mode=mode)))
    manager.request(A, "F", levels[0], since[0])
    manager.request(B, "D", levels[1], since[1])
    assert manager.tick(0.9, reports).grants == []
    loser = B if winner == A else A
    expected = [winner, loser] if mode == "SHARED_YIELD" else [winner]
    assert [leg.robot for leg in manager.tick(1, reports).grants] == expected
    corridor = manager.snapshot()["CORRIDOR"]
    assert corridor["holder"] == winner and corridor["state"] == "GRANTED"
    assert corridor["capacity"] == len(expected) and corridor["holders"] == expected
    if mode == "EXCLUSIVE":
        assert loser in manager.pending and winner in manager.wait_reasons[loser]
        assert [item["robot"] for item in corridor["queue"]] == [loser]
    else:
        assert not manager.pending and not manager.wait_reasons and not corridor["queue"]
    grant = next(e for e in manager.events if e["type"] == "RESOURCE_GRANT")
    assert grant["detail"]["candidates"] == [winner, loser]
    for event in manager.events:
        assert parse_event(dumps(event)) == event and event["detail"]["reason"]


def test_3_higher_priority_never_preempts(manager, reports):
    first = manager.request(A, "F", 1, 0)
    assert manager.tick(1, reports).grants == [first]
    manager.request(B, "D", 3, 1)
    assert manager.tick(10, reports).grants == []
    assert manager.resources["CORRIDOR"].holder == A


def test_4_6_occupied_destination_blocks_entire_set(manager, cfg, reports):
    reports[A] = RobotReport(cfg.zones["B"], True)
    manager.request(B, "B", 3, 0)
    assert manager.tick(1, reports).grants == []
    assert manager.resources["ROOM_B"].holder == A
    assert manager.resources["ROOM_B"].state == "OCCUPIED"
    assert manager.resources["CORRIDOR"].state == "FREE"
    assert A in manager.wait_reasons[B]


def test_7_continuous_clearance_margin_and_release_window(manager, reports, cfg):
    reports[A] = RobotReport(MID, True)
    manager.request(B, "D", 1, 0)
    manager.tick(0, reports)
    reports[A] = RobotReport(cfg.zones["D"], True)
    manager.tick(1, reports)
    reports[A] = RobotReport((-0.99, -2.4), True)  # outside, still within margin
    manager.tick(1.9, reports)
    reports[A] = RobotReport(cfg.zones["D"], True)
    manager.tick(2, reports)
    manager.tick(2.99, reports)
    assert manager.resources["CORRIDOR"].state == "OCCUPIED"
    assert manager.tick(3, reports).grants == []
    assert manager.resources["CORRIDOR"].state == "FREE"
    assert manager.tick(3.99, reports).grants == []
    assert [leg.robot for leg in manager.tick(4, reports).grants] == [B]


@pytest.mark.parametrize("entered", [True, False])
def test_8_offline_holder_requires_logged_clear(manager, reports, cfg, entered):
    first = manager.request(A, "F", 1, 0, mission_id="M-1")
    manager.tick(1, reports)
    if entered:
        reports[A] = RobotReport(MID, True)
        manager.tick(2, reports)
        assert manager.resources["CORRIDOR"].state == "OCCUPIED"
        reports[A] = RobotReport(cfg.zones["F"], True)
        manager.tick(3, reports)
    reports[A] = RobotReport(cfg.zones["F"], False)
    second = manager.request(B, "D", 3, 3)
    assert manager.tick(4, reports).grants == []
    assert manager.resources["CORRIDOR"].state == "UNKNOWN"
    assert manager.finish(first, 4)
    reports[A] = RobotReport(cfg.zones["F"], True)
    assert manager.tick(20, reports).grants == []
    assert manager.resources["CORRIDOR"].state == "UNKNOWN"
    warning = next(e for e in manager.events if e["type"] == "RESOURCE_UNKNOWN")
    assert warning["detail"]["code"] == "E-423"
    with pytest.raises(ValueError, match="E-400"):
        manager.clear_unknown("CORRIDOR", " ", 20)
    manager.clear_unknown("CORRIDOR", "nurse01", 20)
    assert manager.resources["CORRIDOR"].state == "FREE"
    assert manager.tick(21, reports).grants == [second]
    event = next(e for e in manager.events if e["type"] == "RESOURCE_CLEAR_CONFIRM")
    assert event["detail"]["by"] == "nurse01"


def test_unused_grants_returned_but_occupied_room_retained(manager, reports, cfg):
    reports[A] = RobotReport(cfg.zones["B"], True)
    leg = manager.request(A, "E", 1, 0)
    assert manager.tick(1, reports).grants == [leg]
    assert manager.finish(leg, 1)
    assert manager.resources["ROOM_B"].state == "OCCUPIED"
    assert manager.resources["ROOM_E"].state == manager.resources["CORRIDOR"].state == "FREE"
    assert not manager.finish(leg, 2)


def test_safe_pause_blocks_grants_and_preserves_occupied_resources(manager, reports, cfg):
    reports[A] = RobotReport(cfg.zones["B"], True, safe_pause=True)
    leg = manager.request(A, "F", 2, 0)
    assert manager.tick(1, reports).grants == []
    reports[A] = RobotReport(cfg.zones["D"], True, safe_pause=True)
    assert manager.tick(3, reports).grants == []
    assert manager.tick(5, reports).grants == []
    assert manager.resources["ROOM_B"].holder == A
    reports[A] = replace(reports[A], safe_pause=False)
    assert manager.tick(6, reports).grants == [leg]


@pytest.mark.parametrize("mode", ["EXCLUSIVE", "SHARED_YIELD"])
def test_aging_and_blocked_resource(cfg, reports, mode):
    manager = ResourceManager(replace(cfg, corridor=replace(cfg.corridor, mode=mode),
                                      resources=replace(cfg.resources, aging_s=1)))
    oldest = manager.request(A, "F", 1, 0)
    manager.resources["CORRIDOR"].blocked_by = B
    assert manager.tick(2, reports).grants == []
    newer = manager.request(B, "D", 3, 2.5)
    manager.resources["CORRIDOR"].blocked_by = None
    expected = [oldest, newer] if mode == "SHARED_YIELD" else [oldest]
    assert manager.tick(3, reports).grants == expected


def test_conflicting_reports_lock_resource(manager, reports):
    reports = {robot: RobotReport(MID, True) for robot in reports}
    manager.tick(0, reports)
    assert manager.resources["CORRIDOR"].state == "UNKNOWN"


def test_hall_trip_skips_window_and_offline_waits(manager, reports):
    leg = manager.request(A, "A", 2, 0)
    assert manager.tick(0, {}).grants == []
    assert manager.tick(0.1, reports).grants == [leg]


def test_same_tick_arrival_preserves_newly_entered_room(manager, reports, cfg):
    leg = manager.request(A, "E", 1, 0)
    assert manager.tick(1, reports).grants == [leg]
    reports[A] = RobotReport(cfg.zones["E"], True)
    manager.observe(2, reports)
    assert manager.finish(leg, 2)
    manager.request(B, "E", 3, 2)
    assert manager.arbitrate(2).grants == []
    assert manager.resources["ROOM_E"].state == "OCCUPIED"
    assert manager.resources["CORRIDOR"].state == "FREE"


def test_retry_inside_corridor_reuses_own_lock(manager, reports):
    reports[A] = RobotReport(MID, True)
    leg = manager.request(A, "D", 1, 0)
    assert manager.tick(0, reports).grants == [leg]
    assert manager.resources["CORRIDOR"].state == "OCCUPIED"
    assert manager.resources["CORRIDOR"].holder == A


def test_pause_excludes_from_candidates_but_keeps_queue(manager, reports):
    reports[A] = replace(reports[A], safe_pause=True)
    manager.request(A, "F", 3, 0)
    leg = manager.request(B, "D", 1, 0)
    assert manager.tick(1, reports).grants == [leg]
    assert manager.events[-1]["detail"]["candidates"] == [B]
    assert manager.snapshot()["CORRIDOR"]["queue"] == [{"robot": A, "level": 3, "since": 0}]


@pytest.mark.parametrize("a_level,b_level,rule", [(1,3,"LEVEL"), (2,2,"REQUEST_TIME")])
def test_31_contention_basis(manager, reports, a_level, b_level, rule):
    manager.request(A,"F",a_level,0,mission_id="M-1",mission_type="CALL")
    manager.request(B,"D",b_level,.1,mission_id="M-2",mission_type="MED_ROUND")
    manager.tick(1,reports)
    for kind in ("RESOURCE_GRANT","RESOURCE_HOLD"):
        decision = next(e for e in manager.events if e["type"] == kind)
        assert decision["detail"]["rule"] == rule
        assert {c["robot"] for c in decision["detail"]["contenders"]} == {A,B}
        assert {c["type"] for c in decision["detail"]["contenders"]} == {"CALL","MED_ROUND"}
        assert decision["detail"]["state_before"] == ("FREE" if kind == "RESOURCE_GRANT" else "GRANTED")


def test_31_late_higher_level(manager, reports):
    manager.request(A,"F",1,0)
    manager.tick(1,reports)
    manager.request(B,"D",3,2)
    manager.tick(3,reports)
    assert manager.last_decision["detail"]["rule"] == "NO_PREEMPT"
