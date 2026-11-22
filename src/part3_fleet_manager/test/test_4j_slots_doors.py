"""4J: two slots per zone, B·E doors and the shared crossroad (BRIEF 5.2–5.3, D-30). Fake clock."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"
CROSSROAD = (-1.5, -2.18)


@pytest.fixture
def make(cfg, tmp_path):
    sims = []

    def build(*, slots=True, robots=None, **changes):
        config = replace(cfg, db_path=str(tmp_path / f"bad{len(sims)}.db"), **changes)
        if robots:
            config = replace(config, robots=robots)
        sim = Simulation(config, slots=slots)
        sims.append(sim)
        return sim
    yield build
    for sim in sims:
        sim.core.store.close()


def commands(out):
    return {c.robot: c.command for c in out.commands}


def moves(sim, robot, start, end, step=0.5):
    """Tick from start to end and return the MOVE commands sent to one robot."""
    sent, t = [], start
    while t <= end + 1e-9:
        command = commands(sim.tick(round(t, 3))).get(robot)
        if command and command.startswith("MOVE"):
            sent.append(command)
        t += step
    return sent


def status(sim, robot, name, **fields):
    frame = sim.frames[robot]
    sim.pending.append(("status", json.dumps({"type": "STATUS", "event": name, "robot_id": frame["robot_id"],
                                              "mission_id": frame["mission_id"], **fields}), robot))


def slot_pose(sim, zone, slot):
    return sim.pose(sim.cfg.slot_point(zone, slot))


def types(out):
    return [e["type"] for e in out.events]


def arrive(sim, robot, t):
    sim.event(robot, "ARRIVED")
    return sim.tick(t)


# --- 11/19 field record (BRIEF 9 #55) ------------------------------------------------------------

def run_until_move(sim, robot, start, end, step=0.5):
    """Tick until the robot gets a MOVE; return (time, command)."""
    t = start
    while t <= end + 1e-9:
        command = commands(sim.tick(round(t, 3))).get(robot, "")
        if command.startswith("MOVE"):
            return round(t, 3), command
        t += step
    raise AssertionError(f"{robot}: MOVE 없음 ({start}~{end}초)")


def drive(sim, robot, t):
    """MISSION_ACCEPTED, then ARRIVED one second later. Returns the arrival tick output."""
    sim.event(robot, "MISSION_ACCEPTED")
    sim.tick(t)
    return arrive(sim, robot, t + 1)


def test_55_field_1530_1532_1536_second_slot_without_waiting(make):
    sim = make()
    # 15:30 B rests at F (its home, slot 1). A takes a patient from F.
    sim.patient(pickup="F", dest="C")
    t, command = run_until_move(sim, A, 0, 2)
    assert command.startswith("MOVE F2 2 id=")
    assert not any(e["type"] == "ZONE_OCCUPIED_HOLD" for e in sim.core.store.db.execute(
        "SELECT type FROM events").fetchall())
    out = drive(sim, A, t + .1)
    arrived, = [e for e in out.events if e["type"] == "ARRIVE"]
    assert arrived["detail"]["slot"] == 2 and arrived["detail"]["reason"] == "조제실(F) 2번 자리 도착"
    robot = sim.core.snapshot(sim.now)["robots"][A]
    assert robot["zone"] == "F" and robot["slot"] == 2 and sim.state(A) == "WAIT_CONFIRM"
    sim.confirm(A, "p")
    out = sim.tick(sim.now + .1)
    confirm, = [e for e in out.events if e["type"] == "CONFIRM_STEP"]
    assert confirm["detail"]["slot"] == 2
    assert commands(out)[A].startswith("MOVE C2 2 id=")  # C is free: the plan takes its entrance slot
    drive(sim, A, sim.now + .1)
    # 15:32 B stands in D (A's home, slot 1) while A returns home.
    sim.frames[B]["pose"] = slot_pose(sim, "D", 1)
    sim.confirm(A, "p")
    t, command = run_until_move(sim, A, sim.now + .1, sim.now + 3)
    assert command.startswith("MOVE D2 1")
    drive(sim, A, t + .1)
    assert sim.state(A) == "IDLE" and sim.core.snapshot(sim.now)["robots"][A]["slot"] == 2
    # 15:36 the same situation (A home again while B stays in D) gives the same result.
    sim.patient(ref="p2", pickup="C", dest="F")
    t, command = run_until_move(sim, A, sim.now + .1, sim.now + 3)
    assert command.startswith("MOVE C")
    drive(sim, A, t + .1)
    sim.confirm(A, "p2")
    t, command = run_until_move(sim, A, sim.now + .1, sim.now + 3)
    assert command.startswith("MOVE F")  # B left F: either slot
    drive(sim, A, t + .1)
    sim.confirm(A, "p2")
    t, command = run_until_move(sim, A, sim.now + .1, sim.now + 3)
    assert command.startswith("MOVE D2 1")


def test_55_robot_waiting_for_stop_confirmation_keeps_slot1(make):
    sim = make()
    sim.call("call", "A")  # B goes to A and waits for [수령 완료] there
    assert commands(sim.tick(0))[B].startswith("MOVE A2 1")    # the plan: the entrance slot, A1 stays free
    sim.tick(1)
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(1.1)
    arrive(sim, B, 2)
    assert sim.state(B) == "WAIT_CONFIRM"
    sim.patient(pickup="A", dest="C")  # A comes from D to the same zone: the other slot, B is not moved
    out = sim.tick(2.1)
    assert commands(out)[A].startswith("MOVE A 2") and B not in commands(out)


def test_55_c_slots_closer_than_proximity_stop_do_not_stop_each_other(make):
    sim = make()
    sim.frames[B]["pose"] = slot_pose(sim, "C", 1)
    sim.cmd("GO_ZONE", robot=A, zone="C")
    assert commands(sim.tick(0))[A].startswith("MOVE C2 1")
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(1.1)
    # 0.55 m apart at the two C slots: below proximity_stop_m (0.6 m), above safety.slot_stop_m (0.45 m).
    sim.frames[A]["pose"] = slot_pose(sim, "C", 2)
    out = sim.tick(2)
    assert "PROXIMITY_STOP" not in types(out) and not out.commands
    sim.frames[A]["pose"] = sim.pose((-2.75, -4.70))  # 0.42 m: stop
    out = sim.tick(2.5)
    assert "PROXIMITY_STOP" in types(out) and commands(out) == {A: "PAUSE"}


def test_55_leaving_slot1_passes_the_robot_at_slot2(make):
    """F: slot 2 lies between slot 1 and the hall mouth, so the leaving robot passes beside it."""
    sim = make()
    sim.frames[A]["pose"] = slot_pose(sim, "F", 2)  # A rests at F2, B leaves F1
    sim.call("call", "A", level=3)
    assert commands(sim.tick(0))[B].startswith("MOVE ")  # the plan lets B out of F1 beside A at F2
    sim.tick(1)
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(1.1)
    sim.frames[B]["pose"] = sim.pose((-1.85, -4.75))  # 0.50 m beside A
    out = sim.tick(2)
    assert "PROXIMITY_STOP" not in types(out) and not out.commands
    sim.frames[B]["pose"] = sim.pose((-1.75, -4.80))  # 0.41 m: stop
    out = sim.tick(2.5)
    assert "PROXIMITY_STOP" in types(out) and commands(out) == {B: "PAUSE"}


def test_55_slot_stop_needs_a_robot_standing_still(make):
    sim = make()
    sim.cmd("GO_ZONE", robot=A, zone="F")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(1.1)
    sim.frames[A]["pose"] = sim.pose((-1.4, -4.85))  # moving A passes 0.55 m from B standing at F1
    out = sim.tick(2)
    assert "PROXIMITY_STOP" not in types(out)
    sim.frames[B].update(state="MOVING")  # B moving too: the 0.6 m rule again
    out = sim.tick(2.5)
    assert "PROXIMITY_STOP" in types(out) and commands(out).get(A) == "PAUSE"


def test_55_earlier_part4_without_slot_never_gets_a_slot_number(make):
    sim = make(slots=False)
    sim.patient(pickup="F", dest="C")
    # PART 04 before 4J takes no slot number: the plan only goes for slot 1, where robot_b stands; robot_a waits.
    assert not moves(sim, A, 0, 4)
    assert not sim.core.links[A].slots
    sim.frames[B]["pose"] = slot_pose(sim, "E", 1)
    sent = moves(sim, A, 4.5, 12)
    assert sent and all(m.split()[1] == "F" for m in sent)


def test_55_slot_support_is_learned_per_robot_from_state(make):
    sim = make(slots=False)
    sim.frames[A]["slot"] = None  # robot3 runs the 4J PART 04, robot2 the earlier one
    sim.tick(0)
    assert sim.core.links[A].slots and not sim.core.links[B].slots
    sim.frames[A]["pose"] = slot_pose(sim, "A", 1)
    sim.call("call", "A")  # earlier PART 04 on B: never "MOVE A2"
    assert all(m.split()[1] == "A" for m in moves(sim, B, 0.5, 3))
    assert sim.core.resources.slot_support == {A: True, B: False}


def test_56_legacy_robot_without_via_releases_the_door_after_the_crossroad_radius(make):
    sim = make(slots=False)
    sim.frames[A]["pose"] = slot_pose(sim, "B", 1)
    sim.cmd("GO_ZONE", robot=A, zone="D")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    status(sim, A, "NAVIGATING", destination="D")  # earlier PART 04: no via field
    sim.tick(1.1)
    sim.frames[A]["pose"] = sim.pose((-1.95, -2.1))  # through the door
    sim.tick(2)
    robot = sim.core.snapshot(2)["robots"][A]
    assert robot["via"] is None and list(CROSSROAD) not in robot["route"]  # no crossroad leg shown
    sim.frames[A]["pose"] = sim.pose((-1.62, -1.40))
    sim.tick(3)
    assert not sim.core.resources.snapshot()["ROOM_B"]["holders"]


def test_56_via_passed_is_progress_not_arrival_stop_or_failure(make):
    sim = make()
    sim.cmd("GO_ZONE", robot=A, zone="E")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    status(sim, A, "NAVIGATING", destination="E", via="교차로")
    sim.tick(1.1)
    sim.frames[A]["pose"] = sim.pose(CROSSROAD)
    status(sim, A, "VIA_PASSED", destination="E")
    status(sim, A, "NAVIGATING", destination="E", via=None)
    out = sim.tick(2)
    assert not out.commands and not out.cards
    assert not {"ARRIVE", "FAULT", "NAV_FAILED", "PAUSE", "WARNING"} & set(types(out))
    robot = sim.core.snapshot(2)["robots"][A]
    assert sim.state(A) == "MOVING" and robot["part4_state"] == "MOVING" and robot["last_event"] == "NAVIGATING"
    assert robot["via"] == {"point": "교차로", "passed": True} and list(CROSSROAD) not in robot["route"][1:]
    arrive(sim, A, 3)
    assert sim.state(A) == "IDLE" and sim.core.snapshot(3)["robots"][A]["zone"] == "E"


def test_56_unknown_status_event_is_logged_and_does_not_stop(make):
    sim = make()
    sim.cmd("GO_ZONE", robot=A, zone="A")  # same hall: no resource, MOVE at once
    assert commands(sim.tick(0))[A].startswith("MOVE A 1")
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(1)
    status(sim, A, "SQUARING_UP", destination="A")
    out = sim.tick(1.5)
    warning, = [e for e in out.events if e["type"] == "WARNING"]
    assert "SQUARING_UP" in warning["detail"]["diagnostic"] and not out.commands and not out.cards
    assert sim.state(A) == "MOVING"
