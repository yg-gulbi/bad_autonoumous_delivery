"""Stage 4L regressions, with the actual fleet tick, state, event store and wire commands."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

BED, MED = "robot_a", "robot_b"  # robot3 at D, robot2 at F


@pytest.fixture
def sim(cfg, tmp_path):
    fleet = Simulation(replace(cfg, db_path=str(tmp_path / "test.db")), slots=True)
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
    assert sent(sim.tick(0))[MED].startswith("MOVE F")   # the plan's MOVE, in the first tick
    sim.tick(1)
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
    assert sim.core.collision.stop_distance(MED, BED) == sim.cfg.safety.slot_stop_m  # MED is parked at A
    sim.frames[MED]["pose"] = sim.pose((-1.5, -2.4))
    sim.tick(3)
    assert sim.core.collision.stop_distance(MED, BED) == sim.cfg.safety.proximity_stop_m


def test_passing_a_parked_robot_whose_link_drops_keeps_the_slot_distance(sim):
    """4O 정리 (clearance.py): robot_b passes robot_a, parked at D1, at 0.5 m (between slot_stop_m 0.45 and
    proximity_stop_m 0.6, what the plan allows) and robot_a's link drops. It stands still all the same: robot_b is
    not stopped (before: 0.6 m once robot_a was offline, and no release until 0.9 m; random seeds 54 and 248)."""
    out = approach_d(sim, .50)
    assert not out.commands and not sim.core.collision.near
    del sim.frames[BED]                                   # robot_a's STATE stops
    for t in (3, 4, 5.5, 6):
        out = sim.tick(t)
        assert MED not in sent(out), t
    assert not sim.core.link_ok[BED] and not sim.core.collision.near
    assert sim.core.collision.stop_distance(MED, BED) == sim.cfg.safety.slot_stop_m
    assert sim.state(MED) == "MOVING"
