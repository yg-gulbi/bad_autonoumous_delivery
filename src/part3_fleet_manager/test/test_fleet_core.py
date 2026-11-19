"""BRIEF section 9 through the ROS-free input queue and the actual fleet tick."""
import json
import sqlite3
from dataclasses import replace

import pytest

from fleet_simulation import Simulation
from part3_fleet_manager.core.store import Store

A, B = "robot_a", "robot_b"


@pytest.fixture
def sim(cfg, tmp_path, request):
    cfg = replace(cfg, safety=replace(cfg.safety, use_hazard_topic=getattr(request, "param", False)))
    # These stage 2 scenarios, including 26, use fixed patient and medication roles.
    s = Simulation(replace(cfg, corridor=replace(cfg.corridor, mode="SHARED_YIELD"),
                           assignment=replace(cfg.assignment, mode="ROLE"), db_path=str(tmp_path / "bad.db")))
    yield s
    s.core.store.close()


def commands(out):
    return {c.robot: c.command for c in out.commands}


@pytest.fixture
def shared(cfg, tmp_path):
    s = Simulation(replace(cfg, corridor=replace(cfg.corridor, mode="SHARED_YIELD"), assignment=replace(cfg.assignment, mode="IDLE_BEST_BATTERY"), db_path=str(tmp_path / "pool.db")))
    yield s
    s.core.store.close()


@pytest.mark.parametrize(("pct_a", "pct_b", "zone", "expected"), [
    (80, 60, "F", A), (None, 60, "A", B), (20, None, "D", B),
    (19.9, 20, "F", None), (20.01, 20, "F", A), (55, 55, "F", B),
    (None, None, "D", A),
])
def test_27_battery_order_and_threshold(shared, pct_a, pct_b, zone, expected):
    shared.battery(A, pct_a)
    shared.battery(B, pct_b)
    shared.call("call", zone)
    row = shared.tick(0).state["missions"][0]
    assert row["robot"] == expected
    if expected is None:
        assert row["wait_reason"] == "쉬는 로봇 없음"
        shared.battery(B, 85)
        shared.tick(.2)
        assert shared.core.snapshot(.2)["missions"][0]["robot"] == B


def test_27_pool_priority_fifo_tie_and_unassigned_cancel(shared):
    for robot in (A, B):
        shared.battery(robot, 50)
        shared.frames[robot]["pose"] = shared.pose("A")
    for ref, level in (("low", 1), ("urgent1", 3), ("urgent2", 3), ("last", 1)):
        shared.call(ref, level=level)
    state = shared.tick(0).state
    owners = {m["mission_id"]: m["robot"] for m in state["missions"]}
    assert owners == {shared.ids["urgent1"]: A, shared.ids["urgent2"]: B,
                      shared.ids["low"]: None, shared.ids["last"]: None}
    shared.cmd("CANCEL_MISSION", mission_id=shared.ids["low"])
    assert shared.tick(.1).acks[0]["ok"]
    assert shared.core.store.db.execute("SELECT status, robot FROM missions WHERE id=?",
                                        (shared.ids["low"],)).fetchone()[:] == ("CANCELED", None)


def test_28_call_confirmation_home_and_correlated_preemption(shared):
    shared.battery(A, 80)
    shared.battery(B, 60)
    for ref, zone in (("first", "A"), ("second", "F"), ("third", "D")):
        shared.call(ref, zone)
    assert set(commands(shared.tick(0))) == {A, B}
    shared.event(A, "MISSION_ACCEPTED")
    shared.event(A, "ARRIVED")
    shared.tick(.1)
    assert shared.state(A) == "WAIT_CONFIRM"
    shared.confirm(A, "first")
    out = shared.tick(.2)
    assert [e["mission_id"] for e in out.events if e["type"] == "MISSION_DONE"] == [shared.ids["first"]]
    assert not any(e["type"] == "MISSION_ASSIGNED" for e in out.events)
    assert commands(out)[A].startswith("MOVE D 1") and shared.state(A) == "RETURNING"
    shared.event(A, "MISSION_ACCEPTED")
    takeover = shared.tick(.3)
    assert [e["type"] for e in takeover.events if e["type"] in {"LEG_START", "MISSION_ASSIGNED"}] == [
        "LEG_START", "MISSION_ASSIGNED"]
    assert commands(takeover) == {A: "CANCEL"}
    shared.pending.append(("status", json.dumps({"robot_id": "robot2", "type": "STATUS",
                          "event": "MISSION_CANCELED", "mission_id": "old"}), A))
    assert not shared.tick(.4).commands
    shared.event(A, "MISSION_CANCELED")
    assert commands(shared.tick(.5))[A].endswith("id=L-0003-1")
    shared.event(A, "MISSION_ACCEPTED")
    shared.event(A, "ARRIVED")
    shared.tick(.6)
    shared.confirm(A, "third")
    assert commands(shared.tick(.7))[A].startswith("MOVE D 1")
    shared.event(A, "MISSION_ACCEPTED")
    shared.event(A, "ARRIVED")
    shared.tick(.8)
    assert shared.state(A) == "IDLE"
    rows = shared.core.store.db.execute("SELECT robot,status,finished_at FROM missions ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [(A, "DONE", .2), (B, "ACTIVE", None), (A, "DONE", .7)]
    assert shared.core.store.metrics()["missions"][shared.ids["third"]]["arrival"] == .6


@pytest.mark.parametrize("signal", ["ARRIVED", "COMPLETE", "IDLE", "FAILED", "CANCELED", "COMMAND_REJECTED", None])
def test_29_home_cancel_terminal_evidence(shared, signal):
    shared.battery(A, 80)
    shared.battery(B, 20)
    shared.call("first", "A")
    shared.tick(0)
    shared.event(A, "MISSION_ACCEPTED")
    shared.event(A, "ARRIVED")
    shared.tick(.1)
    shared.confirm(A, "first")
    shared.tick(.2)
    shared.event(A, "MISSION_ACCEPTED")
    shared.call("next", "A")
    assert commands(shared.tick(.3)) == {A: "CANCEL"}
    if signal is None:
        assert not shared.tick(3.2).cards
        out = shared.tick(3.4)
        assert not out.commands and shared.state(A) == "FAULT"
        assert [c["kind"] for c in out.cards] == ["NAV_FAILED"]
        return
    if signal == "ARRIVED":
        shared.event(A, "ARRIVED")
    elif signal == "COMMAND_REJECTED":
        shared.pending.append(("status", json.dumps({"type":"STATUS","robot_id":"robot2","mission_id":shared.frames[A]["mission_id"],
                                                      "event":signal,"command":"CANCEL","state":"COMPLETE"}), A))
    else:
        shared.frames[A]["state"] = signal
        if signal == "IDLE":
            shared.frames[A]["mission_id"] = None
    out = shared.tick(.4)
    assert commands(out)[A].startswith("MOVE A ") and not out.cards


def test_role_call_uses_configured_robot(sim):
    sim.call("role", "F")
    sim.battery(B, 10)
    assert set(commands(sim.tick(0))) == {B}


def test_shared_assignment_excludes_offline_and_safety_paused(shared):
    shared.battery(A, 100)
    shared.cmd("PAUSE_ROBOT", robot=A)
    offline = shared.frames.pop(B)
    shared.call("wait")
    assert shared.tick(0).state["missions"][0]["robot"] is None
    shared.frames[B] = offline
    assert shared.tick(1).state["missions"][0]["robot"] == B


@pytest.mark.parametrize("interruptible", [True, False])
def test_dispatch_during_home_move_acceptance(cfg, tmp_path, interruptible):
    cfg = replace(cfg, db_path=str(tmp_path / "test.db"), assignment=replace(cfg.assignment, mode="IDLE_BEST_BATTERY"),
                  mission=replace(cfg.mission, return_interruptible=interruptible))
    sim = Simulation(cfg)
    try:
        sim.battery(A, 90)
        sim.battery(B, 60)
        sim.call("first", "A")
        sim.tick(0)
        sim.event(A, "MISSION_ACCEPTED")
        sim.event(A, "ARRIVED")
        sim.tick(.1)
        sim.confirm(A, "first")
        sim.tick(.2)
        sim.call("next", "D")
        out = sim.tick(.3)
        row, = [m for m in sim.core.snapshot(.3)["missions"] if m["status"] != "DONE"]
        if interruptible:
            assert row["robot"] == A and row["status"] == "QUEUED" and not out.commands
            sim.event(A, "MISSION_ACCEPTED")
            assert commands(sim.tick(.4)) == {A: "CANCEL"}
        else:
            assert row["robot"] == B and sim.state(A) == "RETURNING"
            sim.event(A, "MISSION_ACCEPTED")
            assert A not in commands(sim.tick(.4))
    finally:
        sim.core.store.close()


def test_return_resources_remain_occupied_during_preemption(shared):
    shared.battery(A, 80)
    shared.battery(B, 20)
    shared.call("first", "C")
    shared.tick(0)
    shared.tick(1)
    shared.event(A, "MISSION_ACCEPTED")
    shared.frames[A]["pose"] = shared.pose((-1.55, -2))
    shared.tick(1.1)
    shared.event(A, "ARRIVED")
    shared.tick(1.2)
    shared.confirm(A, "first")
    assert commands(shared.tick(1.3))[A].startswith("MOVE D 1")
    shared.event(A, "MISSION_ACCEPTED")
    shared.frames[A]["pose"] = shared.pose((-1.55, -2.5))
    shared.tick(1.4)
    shared.call("next", "E")
    assert commands(shared.tick(1.5)) == {A: "CANCEL"}
    assert shared.core.resources.snapshot()["CORRIDOR"]["holders"] == [A]
    shared.event(A, "MISSION_CANCELED")
    shared.tick(1.6)
    assert shared.core.resources.snapshot()["CORRIDOR"]["holders"] == [A]
    assert commands(shared.tick(2.7))[A].startswith("MOVE E 1")


@pytest.mark.parametrize("sim", [True], indirect=True)
def test_10_bed_exit_and_20_bump_require_operator_resume(sim):
    sim.patient()
    assert commands(sim.tick(0))[A].startswith("MOVE A 2")
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(.1)
    sim.pending.append(("/patient/bed_exit", True, None))
    assert commands(sim.tick(.2)) == {A: "PAUSE"}
    sim.event(A, "PAUSED")
    assert sim.core.service("/robot/resume", .3)[0] is False
    assert not sim.tick(.3).commands and sim.state(A) == "SAFE_PAUSE"
    sim.pending.append(("/patient/bed_exit", False, None))
    assert not sim.tick(.4).commands
    assert sim.core.service("/robot/resume", .5)[0]
    assert commands(sim.tick(.5)) == {A: "RESUME"}
    sim.event(A, "RESUMED")
    sim.pending.append(("hazards", ["BACKUP_LIMIT"], A))
    assert not sim.tick(.6).commands
    sim.pending.append(("hazards", ["BUMP", "BACKUP_LIMIT"], A))
    out = sim.tick(.7)
    assert commands(out) == {A: "PAUSE"} and any(e["type"] == "BUMP" for e in out.events)
    sim.event(A, "PAUSED")
    sim.core.service("/robot/resume", .8)
    assert not sim.tick(.8).commands and sim.state(A) == "SAFE_PAUSE"
    sim.cmd("RESUME_ROBOT", robot=A)
    assert commands(sim.tick(.9)) == {A: "RESUME"}


def test_11_unanswered_move_is_fault_with_card(sim):
    sim.patient()
    assert "id=" in commands(sim.tick(0))[A]
    assert commands(sim.tick(3)) == {A: "MOVE A 2"}
    out = sim.tick(6)
    assert sim.state(A) == "FAULT" and out.cards[0]["kind"] == "MOVE_REJECTED"
    assert not sim.tick(7).commands


def test_13_failed_navigation_retry_and_duplicate_card(sim):
    sim.patient()
    sim.tick(0)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(.1)
    sim.event(A, "MISSION_FAILED", reason="Nav2: ABORTED")
    card = sim.tick(.2).cards[0]
    assert card["kind"] == "NAV_FAILED" and card["default"] is None and sim.state(A) == "FAULT"
    sim.choose(card, "retry")
    assert sim.tick(.3).acks[0]["ok"]
    out = sim.tick(.4)
    assert commands(out)[A].startswith("MOVE A 2 id=")
    sim.choose(card, "retry")
    assert sim.tick(.5).acks[0]["code"] == "E-409"


def test_14_corridor_cancel_requires_fault_and_retains_occupancy(sim):
    sim.patient(pickup="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.55, -2))
    sim.cmd("CANCEL_MISSION", mission_id=sim.ids["p"])
    assert sim.tick(1.1).acks[0]["code"] == "E-423"
    sim.event(A, "MISSION_FAILED", reason="test")
    sim.tick(1.2)
    sim.cmd("CANCEL_MISSION", mission_id=sim.ids["p"])
    assert sim.tick(1.3).acks[0]["ok"]
    assert sim.core.resources.snapshot()["CORRIDOR"]["holders"] == [A]
    sim.frames[A]["pose"] = sim.pose("D")
    sim.tick(2)
    sim.tick(3)
    assert sim.core.resources.snapshot()["CORRIDOR"]["state"] == "FREE"


def test_16_mismatch_requires_choice_once(sim):
    sim.med()
    sim.tick(0)
    sim.event(B, "MISSION_ACCEPTED")
    sim.event(B, "ARRIVED")
    sim.tick(.1)
    sim.confirm(B, "m", "MISMATCH")
    out = sim.tick(.2)
    assert not out.commands and out.cards[0]["kind"] == "STOP_MISMATCH"
    sim.confirm(B, "m")
    assert sim.tick(.3).acks[0]["code"] == "E-409"
    sim.choose(out.cards[0], "recheck")
    assert sim.tick(.4).acks[0]["ok"] and sim.state(B) == "WAIT_CONFIRM"


def test_9_stall_blocks_new_grant_then_resolves_on_motion(sim):
    sim.patient(pickup="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.55, -1.7))
    sim.tick(2)
    sim.tick(3)
    assert not any(e["type"] == "STALL_SHORT" for e in sim.tick(11.9).events)
    short = sim.tick(12)
    assert [e["type"] for e in short.events].count("STALL_SHORT") == 1 and not short.cards
    assert not sim.tick(31.9).cards
    sim.med(stops=["A"])
    long = sim.tick(32)
    assert {"STALL_LONG", "BLOCKED"} <= {e["type"] for e in long.events}
    assert long.cards[0]["default"] is None and not sim.tick(34).commands
    assert sim.core.resources.snapshot()["CORRIDOR"]["blocked"] and sim.state(B) == "HOLD"
    sim.frames[A]["pose"] = sim.pose((-1.55, -2.1))
    out = sim.tick(35)
    assert {"UNBLOCKED", "CARD_CLOSED"} <= {e["type"] for e in out.events}
    assert commands(out)[B].startswith("MOVE A 3") and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]


def encounter(sim):
    sim.patient(pickup="C", dest="A")
    sim.med()
    assert set(commands(sim.tick(0))) == {B}
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(.1)
    sim.event(B, "ARRIVED")
    sim.tick(.2)
    sim.confirm(B, "m")
    sim.tick(.3)
    sim.patient(ref="p2", pickup="D")
    sim.med(ref="m2", stops=["F"])
    sim.tick(.4)
    assert set(commands(sim.tick(1))) == {A, B}
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(1.1)
    sim.frames[A]["pose"] = sim.pose((-1.55, -1.7))
    sim.frames[B]["pose"] = sim.pose((-1.55, -2.7))
    assert commands(sim.tick(2)) == {A: "PAUSE"}
    assert sim.state(A) == "GIVE_WAY" and sim.state(B) == "MOVING"
    sim.event(A, "PAUSED")


def test_25_bed_exit_preempts_give_way_rechecks_before_resume(sim):
    encounter(sim)
    sim.pending.append(("/patient/bed_exit", True, None))
    assert not sim.tick(2.1).commands and sim.state(A) == "SAFE_PAUSE"
    sim.pending.append(("/patient/bed_exit", False, None))
    assert not sim.tick(2.2).commands
    sim.cmd("RESUME_ROBOT", robot=A)
    assert not sim.tick(2.3).commands and sim.state(A) == "GIVE_WAY"
    sim.frames[B]["pose"] = sim.pose("E")
    assert commands(sim.tick(2.4)) == {A: "RESUME"}
    assert sim.state(A) == "MOVING"


def test_failure_while_giving_way_waits_for_operator(sim):
    encounter(sim)
    sim.event(A, "MISSION_FAILED", reason="Nav2: ABORTED")
    out = sim.tick(2.1)
    assert sim.state(A) == "FAULT" and out.cards[0]["kind"] == "NAV_FAILED"
    assert not out.commands and sim.core.resources.snapshot()["CORRIDOR"]["give_way"] is None


def test_15_26_concurrent_transport_round_and_queues(sim):
    encounter(sim)
    sim.event(B, "ARRIVED")
    assert commands(sim.tick(2.1)) == {A: "RESUME"}
    assert sim.state(B) == "WAIT_CONFIRM"
    sim.event(A, "RESUMED")
    sim.event(A, "ARRIVED")
    sim.tick(2.2)
    assert sim.state(A) == "WAIT_CONFIRM"
    sim.confirm(B, "m")
    sim.confirm(A, "p")
    next_moves = commands(sim.tick(2.3))
    next_moves.update(commands(sim.tick(3.3)))
    # The preceding leg's clearance timer cannot release A's new return reservation.
    assert A in sim.core.resources.snapshot()["CORRIDOR"]["holders"]
    next_moves.update(commands(sim.tick(4.4)))
    assert next_moves[A].startswith("MOVE A 2") and next_moves[B].startswith("MOVE B 3")
    assert sim.core.resources.snapshot()["ROOM_B"]["holders"] == [B]
    assert sim.core.resources.snapshot()["ROOM_E"]["holders"] == [B]
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(4.5)
    sim.frames[A]["pose"] = sim.pose((-1.65, -3.7))
    sim.frames[B]["pose"] = sim.pose((-1.55, -2.2))
    assert not sim.tick(4.6).commands
    corridor = sim.core.resources.resources["CORRIDOR"]
    assert {r: h.state for r, h in corridor.holders.items()} == {A: "OCCUPIED", B: "OCCUPIED"}
    sim.event(B, "ARRIVED")
    sim.tick(5)
    assert sim.state(A) == "MOVING" and sim.state(B) == "WAIT_CONFIRM"
    assert sim.core.resources.snapshot()["ROOM_B"]["state"] == "OCCUPIED"
    sim.confirm(B, "m")
    assert commands(sim.tick(5.1))[B].startswith("MOVE F 1")
    sim.event(B, "MISSION_ACCEPTED")
    sim.event(A, "ARRIVED")
    assert commands(sim.tick(5.2))[B] == "CANCEL"
    sim.event(B, "MISSION_CANCELED")
    sim.confirm(A, "p")
    out = commands(sim.tick(5.3))
    assert out[A].startswith("MOVE D 1") and out[B].startswith("MOVE F 3")
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(B, "MISSION_ACCEPTED")
    assert commands(sim.tick(5.4)) == {A: "CANCEL"}
    sim.event(A, "MISSION_CANCELED")
    assert commands(sim.tick(5.5))[A].startswith("MOVE D 2")
    state = sim.core.snapshot(5.5)
    assert state["robots"][A]["mission_id"] == sim.ids["p2"]
    assert state["robots"][B]["mission_id"] == sim.ids["m2"]
    assert not sim.tick(6.4).commands and sim.state(B) == "MOVING"
    assert sim.core.resources.snapshot()["ROOM_E"]["state"] == "FREE"
    db = sim.core.store.db
    assert {r[0] for r in db.execute("SELECT id FROM missions WHERE status='DONE'")} == {sim.ids["p"], sim.ids["m"]}
    assert db.execute("SELECT count(*) FROM patients").fetchone()[0] == 1
    assert db.execute("SELECT count(DISTINCT patient_id) FROM missions").fetchone()[0] == 1
    for key in ("p", "m"):
        trip = sim.core.store.metrics()["missions"][sim.ids[key]]
        assert trip["departure"] is not None and trip["arrival"] is not None
    assert state["kpi"]["pause_count"] >= 1


def test_invalid_request_ack_and_state_rate(sim):
    sim.pending.append(("/ui/mission_req", "{", None))
    out = sim.tick(0)
    assert out.acks[0]["ref"] is None and out.acks[0]["code"] == "E-400"
    assert out.state is not None and sim.tick(.1).state is None and sim.tick(.2).state is not None


def test_five_hz_publication_with_timer_jitter(sim):
    stamps = [100.0 + i / 10 + (i % 3) * .0001 for i in range(100)]
    assert sum(sim.tick(t).state is not None for t in stamps) == 50


def test_sqlite_writer_lock_preserves_pause_and_retries_events_once(sim):
    sim.patient()
    sim.tick(0)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(.1)
    writer = sqlite3.connect(sim.cfg.db_path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        sim.pending.append(("/patient/bed_exit", True, None))
        out = sim.tick(.2)
        assert out.storage_error and commands(out) == {A: "PAUSE"}
        assert writer.execute("SELECT count(*) FROM events WHERE type='PAUSE'").fetchone()[0] == 0
    finally:
        writer.close()
    sim.event(A, "PAUSED")
    assert sim.tick(.3).storage_error is None
    sim.tick(.4)
    assert sim.core.store.db.execute("SELECT count(*) FROM events WHERE type='PAUSE'").fetchone()[0] == 1
    assert sim.core.store.metrics()["pause_count"] == 1


def test_operator_card_cancellation_is_persisted_once(sim):
    sim.med(stops=["F"])
    sim.tick(0)
    sim.event(B, "MISSION_ACCEPTED")
    sim.event(B, "ARRIVED")
    sim.tick(.1)
    sim.confirm(B, "m", "MISMATCH")
    card = sim.tick(.2).cards[0]
    sim.choose(card, "cancel")
    sim.choose(card, "cancel")
    out = sim.tick(.3)
    assert [a["code"] for a in out.acks] == [None, "E-409"] and sim.state(B) == "IDLE"
    row = sim.core.store.db.execute('SELECT choice, "by", close_reason, closed_at FROM cards WHERE id=?',
                                    (card["card_id"],)).fetchone()
    assert tuple(row) == ("cancel", "nurse", "selected", .3)


def test_cancel_unentered_grant_immediately_returns_slot_and_serializes_next_move(sim):
    sim.patient(pickup="C")
    sim.patient(ref="next", pickup="D")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(1.1)
    sim.cmd("CANCEL_MISSION", mission_id=sim.ids["p"])
    out = sim.tick(1.2)
    assert out.acks[0]["ok"] and commands(out) == {A: "CANCEL"}
    assert sim.core.resources.snapshot()["CORRIDOR"]["holders"] == []
    assert any(e["type"] == "MISSION_CANCELED" for e in out.events)
    assert not sim.tick(1.3).commands
    sim.event(A, "MISSION_CANCELED")
    assert commands(sim.tick(1.4))[A].startswith("MOVE D 2")


def test_go_zone_uses_manual_level_and_shared_resource_rules(sim):
    sim.cmd("GO_ZONE", robot=A, zone="B")
    assert not sim.tick(0).commands
    sim.cmd("GO_ZONE", robot=A, zone="F")
    out = sim.tick(1)
    assert out.acks[0]["code"] == "E-409"
    assert commands(out)[A].startswith("MOVE B 1 ")
    assert sim.core.resources.snapshot()["ROOM_B"]["holders"] == [A]
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(A, "ARRIVED")
    sim.tick(1.1)
    assert sim.state(A) == "IDLE"


def test_equal_resource_requests_follow_input_order(sim):
    sim.med(stops=["B"], level=2)
    sim.patient(pickup="B", level=2)
    assert not sim.tick(0).commands
    assert set(commands(sim.tick(1))) == {B}
    assert sim.state(A) == "HOLD"


def test_two_robot_firmware_versions_have_independent_protocol_sessions(sim):
    sim.patient(pickup="D", dest="A")
    sim.med(stops=["F", "F"])
    first = commands(sim.tick(0))
    assert all(" id=" in first[r] for r in (A, B))
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(.1)
    assert commands(sim.tick(3)) == {B: "MOVE F 3"}
    sim.frames[B].update(mission_id="9f2c1a0b3d4e", state="MOVING", destination="F")
    sim.pending.append(("status", json.dumps({"type": "STATUS", "event": "MISSION_ACCEPTED",
                        "robot_id": sim.cfg.robots[B].ns, "mission_id": "9f2c1a0b3d4e", "destination": "F"}), B))
    sim.tick(3.1)
    sim.event(A, "ARRIVED")
    sim.event(B, "ARRIVED")
    sim.tick(3.2)
    sim.confirm(A, "p")
    sim.confirm(B, "m")
    next_moves = commands(sim.tick(3.3))
    assert next_moves[A].startswith("MOVE A 2 id=") and next_moves[B] == "MOVE F 3"


def test_deadlock_db_measures_until_room_release_and_replays_metrics(sim):
    sim.frames[A]["pose"] = sim.pose("B")
    sim.frames[B]["pose"] = sim.pose("E")
    sim.patient(pickup="E", level=2)
    sim.med(stops=["B"])
    assert not sim.tick(0).commands
    card = sim.tick(2).cards[0]
    assert card["kind"] == "DEADLOCK" and card["default"] == "yield_a"
    sim.tick(22)
    assert sim.state(A) == "YIELDING"
    assert commands(sim.tick(22.1))[A].startswith("MOVE D 2")
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.55, -1.6))
    assert not sim.tick(23).commands
    assert commands(sim.tick(24))[B].startswith("MOVE B 3")
    metrics = sim.core.store.metrics()
    assert metrics["deadlock_count"] == 1 and metrics["deadlock_resolve_s"] == [22]
    assert metrics["missions"][sim.ids["m"]]["level3_wait_s"] == [24]
    assert metrics["missions"][sim.ids["m"]]["hold_s"] == [24]
    # A second connection reads the committed journal and rebuilds the same figures.
    reopened = Store(sim.cfg.db_path)
    try:
        assert reopened.metrics() == metrics
        row = reopened.db.execute("SELECT close_reason, choice FROM cards WHERE id=?", (card["card_id"],)).fetchone()
        assert tuple(row) == ("timeout", "yield_a")
    finally:
        reopened.close()


@pytest.mark.parametrize("choice", ["hold", "retry", "return"])
def test_blocked_choices_respect_protocol_state(sim, choice):
    sim.patient(pickup="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.55, -1.7))
    sim.tick(2)
    card = sim.tick(32).cards[0]
    assert card["options"][0] == {"id": "retry", "label": "확인함, 다시 출발"}
    if choice == "retry":
        sim.event(A, "PAUSED")
    sim.choose(card, choice)
    out = sim.tick(32.1)
    assert out.acks[0]["ok"]
    if choice == "hold":
        assert commands(out) == {A: "PAUSE"} and sim.state(A) == "FAULT"
        assert {"FAULT", "PAUSE", "CARD_CLOSED"} <= {e["type"] for e in out.events}
    elif choice == "retry":
        assert commands(out) == {A: "RESUME"} and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    else:
        assert commands(out) == {A: "CANCEL"}
        sim.event(A, "MISSION_CANCELED")
        assert commands(sim.tick(32.2))[A].startswith("MOVE D 2")
        sim.event(A, "MISSION_ACCEPTED")
        sim.event(A, "ARRIVED")
        sim.tick(32.3)
        assert sim.state(A) == "FAULT"
        sim.cmd("RETRY", robot=A)
        assert commands(sim.tick(32.4))[A].startswith("MOVE C 2")


def test_30_route_since_and_recent_history(shared):
    shared.battery(A, 80)
    shared.battery(B, 20)
    shared.call("main", "C")
    out = shared.tick(0)
    row = out.state["missions"][0]
    assert row["requested_by"] == "nurse" and row["created_at"] == 0
    route = out.state["robots"][A]["route"]
    assert route == [list(shared.cfg.zones["D"]), list(shared.cfg.corridor.mouths["TOP"]),
                     list(shared.cfg.corridor.mouths["BOTTOM"]), list(shared.cfg.zones["C"])]
    road = out.state["resources"]["CORRIDOR"]
    assert road["queue_detail"] == [{"robot": A, "level": 1, "since": 0, "mission_id": row["mission_id"]}]
    assert road["reason"] == out.state["last_decision"]["detail"]["reason"]
    assert shared.tick(.2).state["robots"][A]["state_since"] == 0
    for i in range(21):
        shared.call(str(i))
    shared.tick(.3)
    for i in range(21):
        shared.cmd("CANCEL_MISSION", mission_id=shared.ids[str(i)])
    shared.tick(.4)
    history = [m for m in shared.core.snapshot(.4)["missions"] if m["status"] == "CANCELED"]
    assert [m["mission_id"] for m in history] == [shared.ids[str(i)] for i in range(20, 0, -1)]
    assert all(m["finished_at"] == .4 for m in history)


def bed_frames(sim, start, duration, *, hz=30, value=False):
    """Queue individual receipt stamps and tick the fleet on its own 10 Hz clock."""
    events, wire = [], []
    for i in range(round(duration * hz) + 1):
        stamp = start + i / hz
        sim.core.enqueue("/patient/bed_exit", value, stamp)
        if i % max(1, round(hz / 10)) == 0 or i == round(duration * hz):
            out = sim.tick(stamp)
            events.extend(out.events)
            wire.extend(out.commands)
    return events, wire


def test_35_lost_monitor_pauses_only_target_and_requires_manual_resume(sim):
    sim.patient()
    sim.med(stops=["F"])
    sim.core.enqueue("/patient/bed_exit", False, 0)
    assert set(commands(sim.tick(0))) == {A, B}
    for robot in (A, B):
        sim.event(robot, "MISSION_ACCEPTED")
    bed_frames(sim, .1, 3)
    monitor = sim.core.snapshot(3.1)["bed_exit"]["monitor"]
    assert monitor["state"] == "OK" and monitor["rate_hz"] == pytest.approx(30, abs=.4)
    assert monitor["last_rx"] == 3.1
    assert not sim.tick(4.09).commands
    warn = sim.tick(4.11)
    assert [e["type"] for e in warn.events] == ["BED_MONITOR_WARN"]
    assert not warn.commands and not warn.cards and sim.state(A) == "MOVING"
    assert not sim.tick(5.9).events
    # The service checks the deadline even between fleet ticks.
    assert sim.core.service("/robot/resume", 6.11) == (False, "침상 감시가 끊겨 재개할 수 없습니다. 웹캠을 확인하세요")
    lost = sim.tick(6.11)
    assert commands(lost) == {A: "PAUSE"}
    assert {"BED_MONITOR_LOST", "CARD_OPEN", "RESUME_REJECTED"} <= {e["type"] for e in lost.events}
    card, = lost.cards
    assert card["kind"] == "BED_MONITOR_LOST" and card["robots"] == [A]
    assert card["timeout_s"] is card["default"] is None
    assert {o["id"] for o in card["options"]} == {"hold", "bypass"}
    assert sim.state(A) == "SAFE_PAUSE" and sim.state(B) == "MOVING"
    sim.event(A, "PAUSED")
    sim.cmd("RESUME_ROBOT", robot=A)
    assert sim.tick(6.2).acks[0]["code"] == "E-409"
    partial, wire = bed_frames(sim, 6.3, .5)
    assert not wire and not any(e["type"] == "BED_MONITOR_BACK" for e in partial)
    # A failed restart resets the two-second continuity interval.
    events, wire = bed_frames(sim, 8, 2)
    assert not wire and [e["type"] for e in events].count("BED_MONITOR_BACK") == 1
    closed, = [e for e in events if e["type"] == "CARD_CLOSED"]
    assert closed["detail"]["card_id"] == card["card_id"] and closed["detail"]["closed_by"] == "resolved"
    assert sim.state(A) == "SAFE_PAUSE" and sim.state(B) == "MOVING"
    assert sim.core.service("/robot/resume", 10.1)[0]
    assert commands(sim.tick(10.1)) == {A: "RESUME"}


def test_35_new_mission_during_loss_and_recovery_with_exit(sim):
    bed_frames(sim, 0, .1)
    lost = sim.tick(3.2)
    assert lost.cards and not lost.commands
    sim.patient()
    assert not sim.tick(3.3).commands and sim.state(A) == "SAFE_PAUSE"
    events, wire = bed_frames(sim, 4, 2, value=True)
    assert not wire and {"BED_MONITOR_BACK", "BED_EXIT"} <= {e["type"] for e in events}
    assert not sim.core.service("/robot/resume", 6.1)[0]
    sim.core.enqueue("/patient/bed_exit", False, 6.2)
    assert not sim.tick(6.2).commands and sim.state(A) == "SAFE_PAUSE"
    assert sim.core.service("/robot/resume", 6.3)[0]
    assert commands(sim.tick(6.3))[A].startswith("MOVE ")


@pytest.mark.parametrize("required", [True, False])
def test_36_startup_and_bypass(cfg, tmp_path, required):
    cfg = replace(cfg, db_path=str(tmp_path / "monitor.db"), assignment=replace(cfg.assignment, mode="ROLE"))
    s = Simulation(cfg, require_at_start=required)
    try:
        s.patient()
        first = s.tick(0)
        assert first.state["bed_exit"]["monitor"]["state"] == "NEVER"
        assert first.state["bed_exit"]["monitor"]["last_rx"] is None
        if not required:
            assert any(e["type"] == "BED_MONITOR_WARN" for e in first.events)
            assert not first.cards and commands(first)[A].startswith("MOVE ")
            bed_frames(s, .1, .1)
            first = s.tick(3.3)
        else:
            assert not first.commands and s.state(A) == "SAFE_PAUSE"
        card, = first.cards
        assert s.core.snapshot(s.now)["bed_exit"]["monitor"]["card"] == card
        assert not s.core.service("/robot/resume", 3.4)[0]
        s.choose(card, "bypass")
        out = s.tick(3.4)
        event, = [e for e in out.events if e["type"] == "BED_MONITOR_BYPASS"]
        assert event["detail"]["by"] == "nurse" and s.state(A) == "SAFE_PAUSE"
        assert not out.commands and s.core.snapshot(3.4)["bed_exit"]["monitor"]["bypass"]
        s.choose(card, "bypass")
        assert s.tick(3.5).acks[0]["code"] == "E-409"
        assert s.core.service("/robot/resume", 3.6)[0]
        s.tick(3.6)
        assert s.state(A) != "SAFE_PAUSE"
        events, _ = bed_frames(s, 4, 2)
        assert any(e["type"] == "BED_MONITOR_BACK" for e in events)
        monitor = s.core.snapshot(6)["bed_exit"]["monitor"]
        assert monitor["state"] == "OK" and not monitor["bypass"] and monitor["card"] is None
    finally:
        s.core.store.close()


def test_36_low_rate_and_hold(sim):
    events, wire = bed_frames(sim, 0, 4, hz=5)
    assert [e["detail"]["reason"] for e in events if e["type"] == "BED_MONITOR_WARN"] == ["침상 감시 불안정"]
    m = sim.core.snapshot(4)["bed_exit"]["monitor"]
    assert m["warning"] == "rate" and m["rate_hz"] == 5 and not wire
    assert sim.core.service("/robot/resume", 4.1)[0]
    card, = sim.tick(7.1).cards
    sim.choose(card, "hold")
    assert sim.tick(7.2).acks[0]["ok"]
    assert not sim.core.service("/robot/resume", 7.3)[0]
    assert not sim.tick(7.3).cards
    assert sim.core.snapshot(7.3)["bed_exit"]["monitor"]["bypass"] is False


def test_37_stop_journal_and_request_replay(shared):
    s = shared
    db = s.core.store.db
    def statuses(mid):
        return [tuple(row) for row in db.execute(
            "SELECT seq,status,arrived_at,confirmed_at,confirmed_by FROM mission_stops WHERE mission_id=? ORDER BY seq", (mid,))]
    s.battery(A, 20)
    s.battery(B, 20)
    s.patient(pickup="D", dest="A")
    request = s.pending[-1]
    s.pending.append(request)
    first = s.tick(0)
    mid = s.ids["p"]
    assert len(first.acks) == 2 and first.acks[0] == first.acks[1]
    assert [r[1] for r in statuses(mid)] == ["PENDING", "PENDING"]
    s.battery(A, 80)
    s.tick(1)
    assert [r[1] for r in statuses(mid)] == ["ACTIVE", "PENDING"]
    assert tuple(db.execute("SELECT assigned_at,started_at FROM missions").fetchone()) == (1, None)
    s.event(A, "MISSION_ACCEPTED")
    s.tick(1.1)
    s.event(A, "ARRIVED")
    s.tick(1.2)
    assert statuses(mid)[0] == (1,"ARRIVED",1.2,None,None)
    s.confirm(A, "p", "MISMATCH")
    mismatch = s.tick(1.3)
    assert statuses(mid)[0][1] == "MISMATCH"
    s.choose(mismatch.cards[0], "recheck")
    s.tick(1.4)
    assert statuses(mid)[0][1] == "ARRIVED"
    assert db.execute("SELECT [by] FROM events WHERE type='CARD_CLOSED' AND mission_id=?", (mid,)).fetchone()[0] == "nurse"
    s.confirm(A, "p")
    s.tick(1.5)
    assert statuses(mid) == [(1,"CONFIRMED",1.2,1.5,"nurse"),(2,"ACTIVE",None,None,None)]
    s.event(A, "MISSION_ACCEPTED")
    s.tick(1.6)
    s.event(A, "ARRIVED")
    s.tick(1.7)
    s.confirm(A, "p")
    s.tick(1.8)
    assert statuses(mid)[1] == (2,"CONFIRMED",1.7,1.8,"nurse")
    assert tuple(db.execute("SELECT status,req_id,started_at,finished_at FROM missions").fetchone()) == ("DONE","p",1.1,1.8)
    assert db.execute("SELECT count(*) FROM events WHERE type='CONFIRM_STEP' AND [by]='nurse'").fetchone()[0] == 3
    # A new controller has no in-memory ACK cache.
    other = Simulation(s.cfg)
    try:
        other.pending.append(request)
        assert other.tick(2).acks == [first.acks[0]]
        assert db.execute("SELECT count(*) FROM missions").fetchone()[0] == 1
    finally:
        other.core.store.close()


def test_37_cancel_after_first_confirmation_preserves_stop(sim):
    sim.patient(pickup="D", dest="A")
    sim.tick(0)
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(A, "ARRIVED")
    sim.tick(.1)
    sim.confirm(A, "p")
    sim.tick(.2)
    sim.cmd("CANCEL_MISSION", mission_id=sim.ids["p"])
    assert sim.tick(.3).acks[0]["ok"]
    db = sim.core.store.db
    assert [r[0] for r in db.execute("SELECT status FROM mission_stops ORDER BY seq")] == ["CONFIRMED","SKIPPED"]
    assert tuple(db.execute("SELECT cancel_reason,finished_at FROM missions").fetchone()) == ("운영자 임무 취소", .3)
    assert db.execute("SELECT [by] FROM events WHERE type='MISSION_CANCELED'").fetchone()[0] == "nurse"


@pytest.mark.parametrize("via_service", [False, True])
def test_42_global_actions_record_actor_and_share_safety_rules(sim, via_service):
    def action(name, t, ok=True):
        if via_service:
            assert sim.core.service("/robot/" + name, t)[0] == ok
        else:
            sim.cmd(name.upper() + "_ALL", by="당직자")
        out = sim.tick(t)
        if not via_service:
            assert out.acks[0]["ok"] == ok
        return out

    actor = "ROS 서비스" if via_service else "당직자"
    sim.patient()
    sim.tick(0)
    for kind, time in (("pause", .1), ("resume", .2)):
        events = [e for e in action(kind, time).events if e["type"] == kind.upper()]
        assert {e["robot"] for e in events} == {A, B}
        assert next(e for e in events if e["robot"] == A)["mission_id"] == sim.ids["p"]
    sim.core.enqueue("/patient/bed_exit", True, .3)
    sim.tick(.3)
    action("resume", .4, False)
    sim.core.enqueue("/patient/bed_exit", False, .5)
    sim.tick(.5)
    sim.tick(4)
    action("resume", 4.1, False)
    db = sim.core.store.db
    assert [r[0] for r in db.execute('SELECT "by" FROM events WHERE type="RESUME_REJECTED"')] == [actor, actor]
    assert [r[0] for r in db.execute('SELECT "by" FROM events WHERE type IN ("PAUSE","RESUME") AND ts<.3')] == [actor]*4


def start_inspection(sim):
    sim.patient(pickup="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.55, -1.7))
    sim.tick(2)
    assert not sim.tick(11.9).inspections
    out = sim.tick(12)
    assert out.inspections == [A] and not out.cards
    assert [e["type"] for e in out.events].count("OBSTACLE_INSPECT") == 1
    assert sim.core.snapshot(12)["robots"][A]["obstacle_pending"]


def camera_answer(label):
    return json.dumps({"status":"ok", "obstacle_present":label is not None,
                       "detections":([] if label is None else [
                           {"label":label,"confidence":.82,"pose":{"x":-1.55,"y":-2.7,"z":0}}])})


@pytest.mark.parametrize("label,kind,reason", [
    ("person","PERSON","사람 감지 (로봇 앞 약 1.0 m)"), ("chair","OBJECT","의자 (로봇 앞 약 1.0 m)"),
    (None,"NONE","카메라에는 장애물이 보이지 않음"), ("silent","UNKNOWN","장애물 분류 불가")])
def test_39_classification_changes_only_block_timing(sim,label,kind,reason):
    start_inspection(sim)
    if label != "silent":
        sim.core.enqueue("obstacle_result",camera_answer(label),12.9,robot=A)
    out = sim.tick(13)
    if label == "silent":
        out = sim.tick(18)
    result, = [e["detail"] for e in out.events if e["type"] == "OBSTACLE_RESULT"]
    assert result["kind"] == kind and result["applied"] and not out.inspections
    assert sim.core.snapshot(sim.now)["robots"][A]["obstacle"]["kind"] == kind
    assert not out.commands
    if kind != "OBJECT":
        assert not out.cards and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
        sim.core.enqueue("obstacle_result",camera_answer("chair"),19,robot=A)
        ignored, = [e for e in sim.tick(19).events if e["type"] == "OBSTACLE_RESULT"]
        assert not ignored["detail"]["applied"]
        assert not sim.tick(31.9).cards
        out = sim.tick(32)
    assert {e["type"] for e in out.events} >= {"STALL_LONG","BLOCKED","CARD_OPEN"}
    card, = out.cards
    assert card["kind"] == "BLOCKED" and reason in card["reason"]
    retry_label = {"PERSON": "비켜 줌, 다시 출발", "OBJECT": "치웠음, 다시 출발"}.get(kind, "확인함, 다시 출발")
    assert card["options"] == [{"id": "retry", "label": retry_label},
                               {"id": "return", "label": "되돌아가기"}, {"id": "hold", "label": "임무 보류"}]
    assert sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    sim.med(stops=["A"])
    sim.tick(33)
    assert not sim.tick(34).commands and sim.state(B) == "HOLD"
    sim.frames[A]["pose"] = sim.pose((-1.55, -2.1))
    moved = sim.tick(35)
    assert commands(moved)[B].startswith("MOVE")
    assert moved.state["robots"][A]["obstacle"] is None
    assert sim.tick(45).inspections == [A]


@pytest.mark.parametrize("interruption", ["failure","safety","motion"])
def test_39_inflight_answer_cannot_override_changed_conditions(sim,interruption):
    start_inspection(sim)
    sim.core.enqueue("obstacle_result",camera_answer("chair"),12.9,robot=A)
    if interruption == "failure":
        sim.event(A,"MISSION_FAILED",reason="Nav2: ABORTED")
    elif interruption == "safety":
        sim.cmd("PAUSE_ALL")
    else:
        sim.frames[A]["percent"] = 10
    out = sim.tick(13)
    assert out.state["robots"][A]["obstacle"] is None
    assert not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    assert not next(e for e in out.events if e["type"]=="OBSTACLE_RESULT")["detail"]["applied"]


def test_39_response_received_before_deadline_survives_tick_delay(sim):
    start_inspection(sim)
    sim.core.enqueue("obstacle_result",camera_answer("person"),18,robot=A)
    sim.core.enqueue("obstacle_result",camera_answer("chair"),18.1,robot=A)
    out = sim.tick(18.2)
    assert [e["detail"]["applied"] for e in out.events if e["type"] == "OBSTACLE_RESULT"] == [True,False]
    assert out.state["robots"][A]["obstacle"]["kind"] == "PERSON" and not out.cards


def test_39_retry_opens_a_new_inspection_window(sim):
    start_inspection(sim)
    sim.core.enqueue("obstacle_result", camera_answer("chair"), 13, robot=A)
    card, = sim.tick(13).cards
    sim.event(A, "PAUSED")
    sim.choose(card, "retry")
    assert commands(sim.tick(14))[A] == "RESUME"
    assert sim.core.snapshot(14)["robots"][A]["obstacle"] is None
    sim.event(A, "RESUMED")
    sim.tick(15)
    assert not sim.tick(24.9).inspections
    assert sim.tick(25).inspections == [A]


def test_39_inflight_inspection_is_per_robot_across_short_stops(sim):
    sim.core.config = replace(sim.cfg, obstacle=replace(sim.cfg.obstacle, inspect_at_s=2, timeout_s=20))
    sim.patient(pickup="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(2)
    assert sim.tick(7).inspections == [A]
    sim.frames[A]["percent"] = 10
    sim.tick(8)
    assert not sim.tick(13).inspections
    sim.core.enqueue("obstacle_result",camera_answer("chair"),13.5,robot=B)
    assert not sim.tick(13.5).cards
    sim.core.enqueue("obstacle_result",camera_answer("chair"),14,robot=A)
    out = sim.tick(14)
    assert out.inspections == [A] and not out.cards and out.state["robots"][A]["obstacle"] is None
