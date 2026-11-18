import json
from dataclasses import replace

import pytest

from part3_fleet_manager.core.robot_protocol import RobotProtocol


def report(protocol, now, **data):
    return protocol.receive(json.dumps({"robot_id": protocol.ns, **data}), now)


def state(protocol, now, name="IDLE", mid=None):
    return report(protocol, now, type="STATE", state=name, mission_id=mid)


@pytest.mark.parametrize(("policy", "first", "second"), [
    ("auto", "MOVE F 3 id=L-1", "MOVE F 3"),
    ("always", "MOVE F 3 id=L-1", "MOVE F 3 id=L-1"),
    ("never", "MOVE F 3", "MOVE F 3"),
])
def test_11_timeout_retries_exactly_once(cfg, policy, first, second):
    p = RobotProtocol("robot2", replace(cfg.robot_link, request_id=policy))
    state(p, 0)
    p.go("L-1", "F", 3)
    assert p.step(0) == (first, None)
    assert p.step(2.99) == (None, None)
    state(p, 3)
    assert p.step(3) == (second, None)
    state(p, 6)
    command, failure = p.step(6)
    assert command is None and failure


@pytest.mark.parametrize("confirm_by_state", [False, True])
def test_12_retarget_paused_waits_for_matching_cancel(cfg, confirm_by_state):
    p = RobotProtocol("robot2", cfg.robot_link)
    state(p, 0, "PAUSED", "previous")
    p.go("L-next", "F", 2)
    assert p.step(0)[0] == "CANCEL"
    assert report(p, .1, type="STATUS", event="MISSION_CANCELED", mission_id="other") is None
    assert p.step(.1)[0] is None
    if confirm_by_state:
        result = state(p, .2, "CANCELED", "previous")
    else:
        result = report(p, .2, type="STATUS", event="MISSION_CANCELED", mission_id="previous")
    assert result["event"] == "CANCEL_CONFIRMED"
    assert p.step(.2)[0] == "MOVE F 2 id=L-next"


@pytest.mark.parametrize("r1", [False, True])
def test_19_auto_remembers_capability_and_ignores_old_events(cfg, r1):
    p = RobotProtocol("robot3", cfg.robot_link)
    state(p, 0)
    p.go("L-1", "F", 2)
    assert p.step(0)[0] == "MOVE F 2 id=L-1"
    wire_id = "L-1" if r1 else "9f2c1a0b3d4e"
    if not r1:
        state(p, 3)
        assert p.step(3)[0] == "MOVE F 2"
    assert report(p, 3, type="STATUS", event="MISSION_ACCEPTED", mission_id=wire_id, destination="F")["event"]
    assert report(p, 3, type="STATUS", event="ARRIVED", mission_id=wire_id, destination="B") is None
    report(p, 3, type="STATUS", event="ARRIVED", mission_id=wire_id, destination="F")
    p.go("L-2", "A", 3)
    state(p, 4, "COMPLETE", wire_id)
    assert p.step(4)[0] == ("MOVE A 3 id=L-2" if r1 else "MOVE A 3")
    assert report(p, 4, type="STATUS", event="MISSION_FAILED", mission_id=wire_id) is None


def test_r1_rejection_is_correlated_and_immediate(cfg):
    p = RobotProtocol("robot2", cfg.robot_link)
    state(p, 0)
    p.go("L-1", "A", 1)
    p.step(0)
    event = dict(type="STATUS", event="COMMAND_REJECTED", state="PAUSED", command="MOVE A 1", reason="busy")
    assert report(p, .1, request_id="old", **event) is None
    assert report(p, .1, request_id="L-1", **event) == {"event": "MOVE_REJECTED", "reason": "busy"}
    p.go("L-2", "B", 1)
    assert p.step(.2)[0] == "CANCEL"
    state(p, .3, "IDLE")
    assert p.step(.3)[0] is None


def test_cancel_unaccepted_move_on_silent_legacy_robot(cfg):
    p = RobotProtocol("robot2", cfg.robot_link)
    state(p, 0)
    p.go("L-1", "F", 1)
    p.step(0)
    p.cancel()
    assert p.step(.1)[0] == "CANCEL"
    assert state(p, 0) is None and p.cancel_wait
    assert state(p, 1) == {"event": "CANCEL_CONFIRMED"} and not p.cancel_wait


def test_foreign_mission_state_does_not_change_correlated_leg(cfg):
    p = RobotProtocol("robot2", cfg.robot_link)
    state(p, 0)
    p.go("L-1", "F", 1)
    p.step(0)
    report(p, .1, type="STATUS", event="MISSION_ACCEPTED", mission_id="L-1", destination="F")
    report(p, .2, type="STATE", state="COMPLETE", mission_id="old", pose={"x": 1, "y": 2}, percent=100)
    assert p.online(.2) and p.pose == (1, 2)
    assert p.state == "MOVING" and p.percent is None
    assert p.step(.2, stop=True)[0] == "PAUSE"


def test_only_state_refreshes_link_and_latest_pose_wins(cfg):
    p = RobotProtocol("robot2", cfg.robot_link)
    p.update_pose({"x": 1, "y": 2}, 1)
    report(p, 0, type="STATE", state="IDLE", pose={"x": 3, "y": 4})
    report(p, 2, type="STATUS", event="FUTURE_EVENT")
    assert p.pose == (1, 2) and p.last_event == "FUTURE_EVENT"
    assert p.online(3) and not p.online(3.1)
    assert p.receive("[1]", 4)["event"] == "WARNING"
