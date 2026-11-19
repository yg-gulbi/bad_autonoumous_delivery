"""4I contract tests against hospital_nav STATE and STATUS frames."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation
from part3_fleet_manager.core.resource_manager import MotionRequest
from part3_fleet_manager.core.robot_protocol import RobotProtocol
from part3_fleet_manager.preflight_checks import state_rows

A, B = "robot_a", "robot_b"


@pytest.fixture
def fleet(cfg):
    sim = Simulation(replace(cfg, db_path=":memory:"))
    yield sim
    sim.core.store.close()


def begin_undock(sim):
    sim.frames[A]["docked"] = True
    sim.patient()
    assert sim.tick(0).commands[0].command.startswith("MOVE A")
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(A, "UNDOCKING")
    sim.frames[A]["state"] = "UNDOCKING"
    assert not sim.tick(0.1).commands
    sim.event(A, "UNDOCKED")
    sim.event(A, "LOCALIZING")
    sim.frames[A].update(state="LOCALIZING", docked=False)
    assert not sim.tick(1).commands


@pytest.mark.parametrize("timeout", [False, True])
def test_50_docked_departure_preparation_timer(fleet, timeout):
    begin_undock(fleet)
    for ts in (5, 12, 35, 90):
        out = fleet.tick(ts)
        assert not (out.inspections or out.cards or out.commands)
        unit = fleet.core.snapshot(ts)["robots"][A]
        assert unit["part4_state"] == "LOCALIZING" and unit["stall_s"] == 0
    if timeout:
        out = fleet.tick(121)
        assert [card["kind"] for card in out.cards] == ["NAV_FAILED"]
        assert out.commands == [MotionRequest(A, "CANCEL")]
        assert not fleet.tick(122).cards
    else:
        fleet.frames[A]["state"] = "MOVING"
        fleet.event(A, "NAVIGATING")
        fleet.tick(91)
        fleet.event(A, "ARRIVED")
        fleet.tick(92)
        assert fleet.state(A) == "WAIT_CONFIRM" and not fleet.tick(122).cards


def test_50_position_protects_navigating_peer_during_localization(fleet):
    begin_undock(fleet)
    fleet.cmd("GO_ZONE", robot=B, zone="C")
    assert fleet.tick(2).commands[0].robot == B
    fleet.event(B, "MISSION_ACCEPTED")
    fleet.frames[B]["pose"] = fleet.pose((-0.6, 0.8))
    assert fleet.tick(3).commands == [MotionRequest(B, "PAUSE")]
    assert fleet.core.snapshot(3)["robots"][A]["part4_state"] == "LOCALIZING"


def test_50_localizing_state_recovers_missed_acceptance(cfg):
    link = RobotProtocol("robot2", cfg.robot_link)
    frame = {"type": "STATE", "robot_id": link.ns, "state": "IDLE", "docked": True}
    link.receive(json.dumps(frame), 0)
    link.go("L-1", "F", 1)
    assert link.step(0)[0] == "MOVE F 1 id=L-1"
    frame.update(state="LOCALIZING", mission_id="L-1", destination="F", docked=False)
    assert link.receive(json.dumps(frame), 1)["event"] == "MISSION_ACCEPTED"
    assert link.move.preparing_since == 1
    assert link.step(3.5) == (None, None)


def test_51_state_and_optional_topic_selection(cfg):
    link = RobotProtocol("robot2", cfg.robot_link)

    def state(stamp, **extra):
        return link.receive(json.dumps(dict(type="STATE", robot_id=link.ns, state="IDLE", **extra)), stamp)

    link.dock_topic(False, 0)
    link.update_battery(10, 0)
    state(1, battery={"percent": 80, "voltage": 15.1, "status": "CHARGING"}, docked=True, pose={"x": 1, "y": 2})
    assert (link.battery, link.battery_status, link.docked, link.pose) == (80, "CHARGING", True, (1, 2))
    link.dock_topic(False, 1.5)
    link.update_battery(3, 0.5)
    assert link.battery == 80 and link.docked is True
    link.update_battery(25, 4)
    state(3, battery={"percent": 17, "status": "DISCHARGING"}, docked=False)
    assert link.battery == 25 and not link.docked and link.battery_status == "DISCHARGING"
    state(5, battery={"percent": 0, "status": "DISCHARGING"})
    assert link.battery == 0
    link.dock_topic(True, 7)
    assert link.docked is True


def test_51_unknown_state_is_reported_once(cfg):
    link = RobotProtocol("robot3", cfg.robot_link)
    frame = json.dumps({"type": "STATE", "robot_id": link.ns, "state": "FUTURE_STATE"})
    assert link.receive(frame, 1)["event"] == "WARNING"
    assert all(link.receive(frame, t) is None for t in range(2, 101))
    assert link.online(101) and link.state == "FUTURE_STATE"
    link.receive(frame.replace("FUTURE_STATE", "IDLE"), 102)
    assert link.receive(frame, 103) is None


@pytest.mark.parametrize("completion", ["DOCKED", "STATE"])
def test_52_dock_home_requires_correlated_physical_completion(cfg, completion):
    unit = replace(cfg.robots[A], return_to="DOCK")
    sim = Simulation(replace(cfg, db_path=":memory:", robots={**cfg.robots, A: unit}))
    mid = "dock-native"

    def status(name, **fields):
        frame = dict(type="STATUS", robot_id="robot2", mission_id=mid, event=name, **fields)
        sim.pending.append(("status", json.dumps(frame), A))

    try:
        sim.patient(pickup="A", dest="D")
        sim.tick(0)
        for ts in (1, 3):
            sim.event(A, "MISSION_ACCEPTED")
            sim.event(A, "ARRIVED")
            sim.tick(ts)
            sim.confirm(A, "p")
            sim.tick(ts + 0.1)
        # B starts beside robot2's measured dock in F, while robot2's home zone is D.
        assert sim.core.collision.zone[A].peer == B
        sim.frames[B]["pose"] = sim.pose("A")
        assert sim.tick(5).commands == [MotionRequest(A, "DOCK")]
        meta = sim.core.snapshot(5)["robots"][A]
        assert meta["leg"]["to"] == "F" and meta["route"][-1] == [-0.330, -5.110]
        status("MISSION_ACCEPTED", destination="DOCK", priority=1)
        status("NAVIGATING", destination="DOCK")
        sim.tick(6)
        status("ARRIVED", destination="DOCK")
        status("DOCKING")
        sim.frames[A].update(state="DOCKING", docked=False, mission_id=mid, destination="DOCK")
        sim.tick(7)
        assert sim.state(A) == "RETURNING"
        sim.frames[A].update(state="COMPLETE", docked=False)
        assert not sim.tick(8).commands and sim.state(A) == "RETURNING"
        sim.frames[A].update(mission_id="old", docked=True)
        sim.tick(9)
        assert sim.state(A) == "RETURNING"
        sim.frames[A].update(mission_id=mid, docked=completion == "STATE")
        if completion == "DOCKED":
            status("DOCKED")
        out = sim.tick(10)
        assert sim.state(A) == "IDLE" and sim.core.links[A].docked is True
        assert len([e for e in out.events if e["type"] == "ARRIVE"]) == 1
        assert not sim.tick(11).commands
    finally:
        sim.core.store.close()


def test_53_hazard_disabled_with_proximity_active(fleet):
    assert not fleet.cfg.safety.use_hazard_topic
    fleet.patient()
    fleet.tick(0)
    fleet.event(A, "MISSION_ACCEPTED")
    fleet.pending.append(("hazards", ["BUMP"], A))
    out = fleet.tick(1)
    assert not out.commands and not fleet.core.safety.causes[A]
    fleet.frames[B]["pose"] = fleet.pose((-0.6, 0.8))
    out = fleet.tick(2)
    assert out.commands == [MotionRequest(A, "PAUSE")]
    assert "PROXIMITY_STOP" in {e["type"] for e in out.events}


@pytest.mark.parametrize("bad", ["battery", "pose", "docked", "state"])
def test_54_preflight_uses_complete_latest_state(bad):
    frame = dict(state="LOCALIZING", battery=dict(percent=0, voltage=None, status="UNKNOWN"),
                 pose=dict(x=0, y=0), docked=False)
    assert all(state_rows(frame).values())
    frame[bad] = None
    assert list(state_rows(frame).values()).count(None) == 1
