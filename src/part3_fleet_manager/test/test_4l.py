"""Stage 4L regressions, with the actual fleet tick, state, event store and wire commands."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

BED, MED = "robot_a", "robot_b"  # robot3 at D, robot2 at F


@pytest.fixture(params=["LEGACY", "SEGMENTS"])
def sim(cfg, tmp_path, request):
    sc = replace(cfg, db_path=str(tmp_path / f"test-{request.param}.db"),
                 traffic=replace(cfg.traffic, mode=request.param))
    fleet = Simulation(sc, slots=True)
    yield fleet
    fleet.core.store.close()


def sent(out):
    return {c.robot: c.command for c in out.commands}


def test_yolo_then_nav_abort(sim):
    sim.call("transfer", "C")
    assert sent(sim.tick(0))[MED].startswith("MOVE C")
    sim.event(MED, "MISSION_ACCEPTED")
    sim.event(MED, "OBSTACLE", source="yolo", **{"class": "box"}, conf=.93, depth_m=.85)
    out = sim.tick(1)
    report, = [e for e in out.events if e["type"] == "OBSTACLE_REPORT"]
    assert report["detail"]["reason"] == "주행 중 장애물 감지 · box 0.93 · 약 0.85 m · 계속 주행"
    assert not out.cards and not out.commands and sim.state(MED) == "MOVING"
    assert not sim.core.reports.failures
    assert sim.core.snapshot(30.99)["robots"][MED]["yolo"]
    assert sim.core.snapshot(31)["robots"][MED]["yolo"] is None

    sim.event(MED, "MISSION_FAILED", reason="Nav2: ABORTED")
    sim.event(MED, "OBSTACLE", image_topic="/robot2/hospital_nav/obstacle_image", lidar_m=.42)
    # Classifications may arrive out of order. The zero-header YOLO JPEG must not
    # consume the failure's single verdict, including after the abort is already open.
    answer = {"status": "ok", "obstacle_present": False, "detections": [],
              "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": ""}}
    sim.core.enqueue("obstacle_result", json.dumps(answer), 2, robot=MED)
    assert not sim.tick(2).cards
    answer.update(header={"frame_id": "oakd", "stamp": {"sec": 2, "nanosec": 0}},
                  obstacle_present=True, detections=[{"label": "medical_box", "confidence": .94}])
    sim.core.enqueue("obstacle_result", json.dumps(answer), 3, robot=MED)
    card, = sim.tick(3).cards
    assert card["kind"] == "BLOCKED" and card["obstacle"]["label"] == "medical_box"
    assert len(sim.core.decisions.cards) == 1


def approach_d(sim, distance):
    sim.frames[MED]["pose"] = sim.pose("A")
    sim.frames[BED]["pose"] = sim.pose((-1.01, .68))
    sim.cmd("GO_ZONE", robot=MED, zone="F")
    sim.tick(0)
    assert sent(sim.tick(1))[MED].startswith("MOVE F")
    sim.event(MED, "MISSION_ACCEPTED")
    sim.frames[MED]["pose"] = sim.pose((-1.01, .68 - distance))
    return sim.tick(2)


def test_through_traffic_passes_parked_d_at_058(sim):
    out = approach_d(sim, .58)
    assert not out.commands and not sim.core.collision.near
    assert sim.state(MED) == "MOVING"


def test_idle_peer_is_not_a_candidate_for_first_departure(sim):
    approach_d(sim, .4)
    sim.event(MED, "PAUSED")
    card, = sim.tick(12).cards
    near = sim.core.snapshot(12)["robots"][MED]["proximity"]
    assert near["self_first"] and not near["peer_first"]
    sim.choose(card, "peer_first")
    out = sim.tick(13)
    assert not out.acks[-1]["ok"] and not out.commands


@pytest.mark.parametrize("approaches_again", [True, False])
def test_self_first_breaks_follow_proximity_wait(sim, approaches_again):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 DIRECTIONAL 정원 2 동시 허가·following 전제 (SEGMENTS에서는 single 교차로 대기로 진입 전 차단)")
    assert sent(approach_d(sim, .40)) == {MED: "PAUSE"}
    sim.event(MED, "PAUSED")
    sim.cmd("GO_ZONE", robot=BED, zone="C")
    assert not sim.tick(3).commands
    assert not sim.tick(4).commands and sim.core.resources.following(BED)
    card, = sim.tick(12).cards
    assert card["context"] == "PROXIMITY"
    near = sim.core.snapshot(12)["robots"][MED]["proximity"]
    assert near["self_first"] and not near["peer_first"]
    sim.choose(card, "peer_first")
    out = sim.tick(12.5)
    assert not out.acks[-1]["ok"] and not out.commands
    sim.choose(card, "self_first")
    assert sent(sim.tick(13)) == {MED: "RESUME"}
    sim.event(MED, "RESUMED")
    sim.frames[MED]["pose"] = sim.pose((-1.01, -.03))
    assert not sim.tick(14).commands
    if approaches_again:
        sim.frames[MED]["pose"] = sim.pose((-1.01, .02))  # 0.71 -> 0.66, still above stop distance
        assert sent(sim.tick(15)) == {MED: "PAUSE"}
    else:
        sim.frames[MED]["pose"] = sim.pose((-1.5, -2.4))  # clears proximity and normal following gap
        assert sent(sim.tick(15))[BED].startswith("MOVE C")
        assert not sim.core.collision.near


@pytest.mark.parametrize("level,when", [(1, 30), (3, 0)])
def test_f1_exit_wait_and_f2_can_leave(sim, level, when):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 EXIT 카드·zone_held 규칙 (SEGMENTS에서는 slot 칸으로 대체)")
    sim.frames[BED]["pose"] = sim.pose(sim.cfg.slot_point("F", 2))
    sim.call("f1", "D", level=level)
    out = sim.tick(0)
    assert not out.commands and sim.core.collision.zone[MED].exit_zone == "F"
    if when:
        assert not out.cards and not sim.tick(when - .01).cards
        out = sim.tick(when)
    card, = out.cards
    assert card["context"] == "EXIT" and [o["id"] for o in card["options"]] == ["retry", "depart", "hold"]
    sim.cmd("GO_ZONE", robot=BED, zone="C")
    assert sent(sim.tick(when+1))[BED].startswith("MOVE C")
    sim.event(BED, "MISSION_ACCEPTED")
    assert MED not in sent(sim.tick(when+2))  # still physically at F2
    sim.frames[BED]["pose"] = sim.pose("C")
    sim.event(BED, "ARRIVED")
    assert sent(sim.tick(when+3))[MED].startswith("MOVE D")
    assert not sim.core.collision.zone and not sim.core.decisions.cards


def test_exit_override_keeps_proximity_before_the_first_move(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 EXIT 카드·depart 선택 규칙")
    sim.frames[MED]["pose"] = sim.pose((-1.40, -5.20))
    sim.frames[BED]["pose"] = sim.pose((-1.35, -4.80))
    sim.call("f1", "D", level=3)
    card, = sim.tick(0).cards
    sim.choose(card, "depart")
    assert not sim.tick(1).commands
    assert not sim.tick(2).commands
    assert MED not in sim.core.collision.zone and MED in sim.core.collision.near
    sim.frames[BED]["pose"] = sim.pose("C")
    assert sent(sim.tick(3))[MED].startswith("MOVE D")


def test_exit_retry_then_hold_at_a(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 EXIT 카드·retry/hold 규칙")
    assert sim.cfg.exit_blocked_zones == {"A", "C", "F"}
    sim.frames[MED]["pose"] = sim.pose("A")
    sim.frames[BED]["pose"] = sim.pose(sim.cfg.slot_point("A", 2))
    sim.call("a1", "F", level=3)
    card, = sim.tick(0).cards
    assert card["context"] == "EXIT"
    sim.choose(card, "retry")
    assert not sim.tick(1).commands
    assert not sim.tick(30).cards
    again, = sim.tick(31).cards
    sim.choose(again, "hold")
    assert not sim.tick(32).commands
    sim.frames[BED]["pose"] = sim.pose("D")
    assert not sim.tick(33).commands and sim.state(MED) == "FAULT"


def test_slot_stop_only_for_parked_peer_without_active_leg(sim):
    sim.frames[MED]["pose"] = sim.pose("A")
    sim.cmd("GO_ZONE", robot=BED, zone="C")
    sim.tick(0)
    sim.tick(1)
    assert BED in sim.core.resources.active
    # A PAUSED peer with a live leg may resume, even at the center of D1.
    sim.event(BED, "MISSION_ACCEPTED")
    sim.event(BED, "PAUSED")
    sim.tick(2)
    assert sim.core.collision.stop_distance(MED, BED, 2) == sim.cfg.safety.slot_stop_m  # MED is parked at A
    sim.frames[MED]["pose"] = sim.pose((-1.5, -2.4))
    sim.tick(3)
    assert sim.core.collision.stop_distance(MED, BED, 3) == sim.cfg.safety.proximity_stop_m
