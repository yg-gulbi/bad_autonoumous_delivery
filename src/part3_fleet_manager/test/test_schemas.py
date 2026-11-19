from copy import deepcopy

import pytest

from part3_fleet_manager.core import schemas


TRANSPORT = {
    "v": 1, "req_id": "ui-20261118-0007", "type": "PATIENT_TRANSPORT", "level": 2,
    "patient": {"name": "홍길동", "chart_no": "P-0012"},
    "pickup": "A", "destination": "C", "requested_by": "nurse01",
}
ROUND = {
    "v": 1, "req_id": "ui-20261118-0008", "type": "MED_ROUND", "level": 1,
    "stops": [{"zone": "A", "item": "인슐린"}, {"zone": "B", "item": "진통제"}],
    "requested_by": "nurse01",
}
COMMAND = {"v": 1, "cmd_id": "c-31", "by": "nurse01"}
CALL = {"v": 1, "req_id": "ui-20261118-0009", "type": "CALL", "level": 1,
        "destination": "A", "item": "거즈", "requested_by": "nurse01"}
DECISION = {"v": 1, "card_id": "K-0012", "choice": "yield_a", "by": "nurse01"}
ACK = {
    "v": 1, "ref": "ui-20261118-0007", "ok": True, "code": None,
    "message": "M-0007 접수, robot_a 배정", "mission_id": "M-0007",
}
CARD = {
    "v": 1, "card_id": "K-0012", "kind": "DEADLOCK", "robots": ["robot_a", "robot_b"],
    "title": "교착: 서로 상대 방으로 가려고 함",
    "reason": "Robot A는 B방에서 E방으로, Robot B는 E방에서 B방으로 가려 함. 서로 상대가 쥔 방을 기다림",
    "options": [{"id": "yield_a", "label": "A가 D 구역으로 비킴"},
                {"id": "yield_b", "label": "B가 F 구역으로 비킴"}],
    "recommended": "yield_a", "timeout_s": 20, "default": "yield_a", "created_at": 1794963604.2,
}
EVENT = {
    "v": 1, "ts": 1794963604.2, "type": "RESOURCE_GRANT", "robot": "robot_b", "mission_id": "M-0006",
    "detail": {"resources": ["CORRIDOR", "ROOM_E"], "reason": "복도 비어 있음 + Level 3 > Level 2 → Robot B 허가"},
}
ROBOT = {
    "label": "Robot A", "ns": "robot2", "link_ok": True, "state": "GIVE_WAY",
    "part4_state": "PAUSED", "region": "CORRIDOR", "zone": None, "pose": {"x": -1.62, "y": -2.90},
    "battery_pct": 82, "mission_id": "M-0007", "leg": {"from": "C", "to": "A"},
    "wait_reason": "복도 양보 중 (Robot B Level 3 > Level 2)", "progress_pct": 38.0,
    "remaining_m": 3.1, "stall_s": 0, "paused_by": "CORRIDOR_YIELD",
}
STATE = {
    "v": 1, "stamp": 1794963604.2,
    "robots": {"robot_a": ROBOT, "robot_b": {**ROBOT, "label": "Robot B", "ns": "robot3",
              "state": "MOVING", "part4_state": "MOVING", "wait_reason": None, "paused_by": None}},
    "resources": {
        "CORRIDOR": {"state": "OCCUPIED", "capacity": 2, "holders": ["robot_b", "robot_a"],
                     "holder": "robot_b", "blocked": False, "queue": [],
                     "give_way": {"yielding": "robot_a", "to": "robot_b", "since": 1794963601.5}},
        "ROOM_B": {"state": "FREE", "capacity": 1, "holders": [], "holder": None, "blocked": False,
                   "queue": [], "give_way": None},
        "ROOM_E": {"state": "GRANTED", "capacity": 1, "holders": ["robot_b"], "holder": "robot_b",
                   "blocked": False, "queue": [], "give_way": None},
    },
    "missions": [{"mission_id": "M-0007", "type": "PATIENT_TRANSPORT", "level": 2, "robot": "robot_a",
                  "status": "ACTIVE", "stops": ["A", "C"], "stop_index": 0}],
    "open_cards": ["K-0012"],
    "bed_exit": {"value": False, "since": 1794963000.0,
                 "monitor": {"state": "OK", "last_rx": 1794963604.1, "rate_hz": 29.8,
                             "since": 1794963000.0, "bypass": False}},
    "kpi": {"hold_count": 3, "deadlock_count": 1, "deadlock_resolve_s_avg": 14.2, "pause_count": 0},
}


@pytest.mark.parametrize(("parse", "payload"), [
    (schemas.parse_mission_req, TRANSPORT),
    (schemas.parse_mission_req, ROUND),
    (schemas.parse_mission_req, CALL),
    (schemas.parse_decision, DECISION),
    (schemas.parse_ack, ACK),
    (schemas.parse_fleet_state, STATE),
    (schemas.parse_decision_req, CARD),
    (schemas.parse_event, EVENT),
])
def test_brief_json_examples(parse, payload):
    assert parse(schemas.dumps(payload)) == payload


@pytest.mark.parametrize("fields", [{"destination": "Z"}, {"destination": None}, {"item": 7}])
def test_invalid_call_fields_are_rejected(fields):
    with pytest.raises(schemas.SchemaError) as exc:
        schemas.parse_mission_req(schemas.dumps({**CALL, **fields}))
    assert exc.value.ack()["code"] == "E-400"


def test_json_preserves_korean_text():
    assert "홍길동" in schemas.dumps(TRANSPORT)
    with pytest.raises(ValueError):
        schemas.dumps({"v": 1, "stamp": float("nan")})


@pytest.mark.parametrize(("command", "fields"), [
    ("CONFIRM_STEP", {"robot": "robot_a", "mission_id": "M-0007", "result": "OK"}),
    ("CONFIRM_STEP", {"robot": "robot_a", "mission_id": "M-0007", "result": "MISMATCH"}),
    ("CANCEL_MISSION", {"mission_id": "M-0007"}),
    ("RETRY", {"robot": "robot_a"}),
    ("PAUSE_ROBOT", {"robot": "robot_a"}),
    ("RESUME_ROBOT", {"robot": "robot_b"}),
    ("GO_ZONE", {"robot": "robot_b", "zone": "F"}),
    ("RESOURCE_CLEAR_CONFIRM", {"resource": "ROOM_E"}),
])
def test_operator_command_fields(command, fields):
    payload = {**COMMAND, "cmd": command, **fields}
    assert schemas.parse_operator_cmd(schemas.dumps(payload)) == payload


@pytest.mark.parametrize("text", ["", "{}", "[]", "null", "{malformed", '{"v":NaN}'])
def test_unreadable_request_returns_e400_without_reference(text):
    with pytest.raises(schemas.SchemaError) as exc:
        schemas.parse_mission_req(text)
    ack = exc.value.ack()
    assert ack["v"] == 1 and ack["ref"] is None and not ack["ok"] and ack["code"] == "E-400"
    assert schemas.parse_ack(schemas.dumps(ack)) == ack


@pytest.mark.parametrize("patch", [
    {"v": True}, {"v": 2}, {"v": 1.0}, {"type": "PATIENT"}, {"type": []},
    {"level": 0}, {"level": 4}, {"level": True}, {"level": 2.0}, {"level": "2"},
    {"pickup": "Z"}, {"destination": ["C"]}, {"patient": {"name": "홍길동"}}, {"requested_by": ""},
])
def test_invalid_transport_returns_e400_with_request_reference(patch):
    with pytest.raises(schemas.SchemaError) as exc:
        schemas.parse_mission_req(schemas.dumps({**TRANSPORT, **patch}))
    ack = exc.value.ack()
    assert ack["ref"] == TRANSPORT["req_id"] and ack["code"] == "E-400"


@pytest.mark.parametrize("stops", [
    [], [{"zone": "A", "item": "인슐린"}] * 7, {"zone": "A"}, ["A"],
    [{"zone": "Z", "item": "인슐린"}], [{"zone": "B"}],
])
def test_invalid_round_stops_return_e400(stops):
    with pytest.raises(schemas.SchemaError, match="stops") as exc:
        schemas.parse_mission_req(schemas.dumps({**ROUND, "stops": stops}))
    assert exc.value.ack()["ref"] == ROUND["req_id"] and exc.value.code == "E-400"


@pytest.mark.parametrize("fields", [
    {"cmd": "OTHER"},
    {"cmd": "GO_ZONE", "robot": "robot2", "zone": "A"},
    {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "Z"},
    {"cmd": "CONFIRM_STEP", "robot": "robot_a", "mission_id": "M-0007", "result": "YES"},
    {"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": "TOP"},
    {"cmd": "CANCEL_MISSION"},
])
def test_invalid_operator_command_returns_e400(fields):
    with pytest.raises(schemas.SchemaError) as exc:
        schemas.parse_operator_cmd(schemas.dumps({**COMMAND, **fields}))
    assert exc.value.ack()["ref"] == COMMAND["cmd_id"] and exc.value.code == "E-400"


def test_invalid_card_choice_returns_e400():
    with pytest.raises(schemas.SchemaError, match="choice") as exc:
        schemas.parse_decision(schemas.dumps({**DECISION, "choice": ""}))
    assert exc.value.ack()["ref"] == DECISION["card_id"] and exc.value.code == "E-400"


def test_fleet_state_can_express_unknowns_and_preserves_unknown_part4_state():
    state = deepcopy(STATE)
    state["bed_exit"] = {"value": None, "since": None}
    state["robots"]["robot_a"].update(pose=None, battery_pct=None, part4_state="FUTURE_STATE")
    assert schemas.parse_fleet_state(schemas.dumps(state)) == state


def test_fleet_state_checks_nested_coordinates():
    state = deepcopy(STATE)
    state["robots"]["robot_a"]["pose"]["x"] = True
    with pytest.raises(schemas.SchemaError, match=r"robots\.robot_a\.pose\.x"):
        schemas.parse_fleet_state(schemas.dumps(state))


def test_card_without_a_default_round_trips():
    payload = {**CARD, "default": None, "timeout_s": None}
    assert schemas.parse_decision_req(schemas.dumps(payload)) == payload


@pytest.mark.parametrize(("name", "patch"), [
    ("ROOM_B", {"capacity": 2}),
    ("CORRIDOR", {"capacity": True}),
    ("CORRIDOR", {"holders": ["robot_b", "robot_b"]}),
    ("CORRIDOR", {"holders": ["robot_b", "robot_a", "robot_c"]}),
    ("CORRIDOR", {"holder": "robot_a"}),
    ("ROOM_E", {"give_way": {"yielding": "robot_b", "to": "robot_a", "since": 1}}),
    ("CORRIDOR", {"give_way": {"yielding": "robot_a", "to": "robot_a", "since": 1}}),
])
def test_resource_capacity_and_give_way_contract(name, patch):
    state = deepcopy(STATE)
    state["resources"][name].update(patch)
    with pytest.raises(schemas.SchemaError):
        schemas.parse_fleet_state(schemas.dumps(state))
