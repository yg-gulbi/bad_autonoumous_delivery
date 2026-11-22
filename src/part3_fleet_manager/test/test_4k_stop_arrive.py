"""4K: PAUSE confirmation and resend, B·E door clear, arrival from STATE, no automatic NONE retry. Fake clock.

The plan sends robot_a's MOVE in the first tick, to C2 (the entrance slot of C: slot2_blocks_exit)."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"
IN_CORRIDOR = (-1.55, -1.7)
STOP_REASON = "침상형 이송 로봇에 정지를 보냈지만 멈췄다는 보고가 없습니다. 현장에서 확인하세요"


@pytest.fixture
def sim(cfg, tmp_path):
    s = Simulation(replace(cfg, db_path=str(tmp_path / "bad.db")), slots=True)
    yield s
    s.core.store.close()


def commands(out):
    return {c.robot: c.command for c in out.commands}


def types(out):
    return [e["type"] for e in out.events]


def moving(sim):
    """A goes D -> C (patient pickup) and is MOVING inside the corridor at t=2."""
    sim.patient(pickup="C", dest="A")
    assert commands(sim.tick(0))[A].startswith("MOVE C2 2")
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose(IN_CORRIDOR)
    sim.tick(2)
    assert sim.state(A) == "MOVING"


def pause_at(sim, t):
    sim.cmd("PAUSE_ROBOT", robot=A)
    out = sim.tick(t)
    assert commands(out)[A] == "PAUSE"
    return out


def run(sim, times):
    """Tick each time; return (times a PAUSE went to A, cards opened, all events)."""
    sent, cards, events = [], [], []
    for t in times:
        out = sim.tick(t)
        if commands(out).get(A) == "PAUSE":
            sent.append(t)
        cards.extend(out.cards)
        events.extend(out.events)
    return sent, cards, events


def steps(start, stop):
    """Half-second ticks in (start, stop]."""
    return [start + i / 2 for i in range(1, int((stop - start) * 2) + 1)]


def stop_cards(sim):
    return [c for c in sim.core.decisions.cards.values() if c["kind"] == "STOP_UNCONFIRMED"]


# ---------------------------------------------------------------- 1. PAUSE confirmation

def test_64_pause_is_resent_when_state_stays_moving_for_retry_time(sim):
    moving(sim)
    pause_at(sim, 3)
    sent, cards, events = run(sim, [3.5, 4.0, 4.4])
    assert not sent and not cards  # 1.5 s not yet passed
    out = sim.tick(4.5)
    assert commands(out)[A] == "PAUSE"
    resent, = [e for e in out.events if e["type"] == "STOP_RESENT"]
    assert resent["robot"] == A and resent["detail"]["attempt"] == 1
    assert resent["detail"]["reason"] == "침상형 이송 로봇 정지 보고 없음 → 정지 다시 보냄 (1번째)"


def test_64_more_than_three_resends_opens_stop_unconfirmed_card(sim):
    moving(sim)
    pause_at(sim, 3)
    sent, cards, events = run(sim, steps(3, 9))
    assert sent == [4.5, 6.0, 7.5]  # three resends, 1.5 s apart
    card, = cards
    assert card["kind"] == "STOP_UNCONFIRMED" and card["title"] == "정지 미확인"
    assert card["reason"] == STOP_REASON and card["robots"] == [A] and card["created_at"] == 9.0
    assert [o["label"] for o in card["options"]] == ["다시 보내기", "현장에서 멈춘 것 확인"]
    assert card["default"] is None
    unconfirmed, = [e for e in events if e["type"] == "STOP_UNCONFIRMED"]
    assert unconfirmed["ts"] == 9.0 and unconfirmed["detail"]["reason"] == STOP_REASON
    assert [e["detail"]["attempt"] for e in events if e["type"] == "STOP_RESENT"] == [1, 2, 3]
    # The card stays the only one; no fourth resend while the operator decides.
    sent, cards, _ = run(sim, steps(9, 14))
    assert not sent and not cards and len(stop_cards(sim)) == 1


def test_64_paused_closes_the_card_and_nothing_more_is_sent(sim):
    moving(sim)
    pause_at(sim, 3)
    _, (card,), _ = run(sim, steps(3, 9))
    sim.event(A, "PAUSED", reason="관제 PAUSE")
    out = sim.tick(9.5)
    closed, = [e for e in out.events if e["type"] == "CARD_CLOSED"]
    assert closed["detail"]["card_id"] == card["card_id"] and closed["detail"]["closed_by"] == "resolved"
    assert not stop_cards(sim)
    sent, cards, events = run(sim, steps(9.5, 15))
    assert not sent and not cards and "STOP_RESENT" not in [e["type"] for e in events]


def test_64_paused_after_one_resend_stops_resending(sim):
    moving(sim)
    pause_at(sim, 3)
    assert run(sim, steps(3, 4.5))[0] == [4.5]
    sim.frames[A]["state"] = "PAUSED"
    sent, cards, _ = run(sim, steps(4.5, 12))
    assert not sent and not cards


def test_64_stopping_pause_is_not_resent(sim):
    moving(sim)
    pause_at(sim, 3)
    sim.frames[A]["state"] = "STOPPING_PAUSE"  # PART 04 took the PAUSE and is stopping
    sent, cards, events = run(sim, steps(3, 12))
    assert not sent and not cards
    assert not {"STOP_RESENT", "STOP_UNCONFIRMED"} & {e["type"] for e in events}


def test_64_card_choices_resend_or_field_confirmed(sim):
    moving(sim)
    pause_at(sim, 3)
    _, (card,), _ = run(sim, steps(3, 9))
    # [다시 보내기]: PAUSE goes out in the same tick and a fresh cycle of resends starts.
    sim.choose(card, "resend")
    out = sim.tick(9.5)
    assert commands(out)[A] == "PAUSE" and not stop_cards(sim)
    closed, = [e for e in out.events if e["type"] == "CARD_CLOSED"]
    assert closed["detail"]["choice"] == "resend" and closed["detail"]["choice_label"] == "다시 보내기"
    sent, cards, _ = run(sim, steps(9.5, 15.5))
    assert sent == [11.0, 12.5, 14.0] and [c["kind"] for c in cards] == ["STOP_UNCONFIRMED"]
    # [현장에서 멈춘 것 확인]: the operator saw it stopped. No more PAUSE, no new card.
    sim.choose(cards[0], "confirmed")
    out = sim.tick(16)
    assert A not in commands(out) and not stop_cards(sim)
    sent, cards, _ = run(sim, steps(16, 25))
    assert not sent and not cards


def test_64_card_closes_when_the_stop_is_no_longer_needed(sim):
    moving(sim)
    pause_at(sim, 3)
    _, (card,), _ = run(sim, steps(3, 9))
    sim.cmd("RESUME_ROBOT", robot=A)
    out = sim.tick(9.5)
    closed, = [e for e in out.events if e["type"] == "CARD_CLOSED"]
    assert closed["detail"]["closed_by"] == "resolved" and not stop_cards(sim)
    assert A not in commands(out)


# ---------------------------------------------------------------- 2. B·E door clear

@pytest.mark.parametrize("zone", ["B", "E"])
def test_65_unknown_segments_are_cleared_by_operator_confirm(sim, zone):
    """robot_a loses the link in the corridor on its way to room B or E: the segments it held go UNKNOWN; the
    operator's RESOURCE_CLEAR_CONFIRM is taken for an UNKNOWN segment and refused for one that is not. (While the
    robot is offline with a MOVE it may still drive, the plan holds the rest of that way again, D-46.)"""
    sim.patient(pickup=zone, dest="A")
    assert commands(sim.tick(0))[A].startswith(f"MOVE {zone}")
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose(IN_CORRIDOR)
    sim.tick(2)
    del sim.frames[A]  # the robot's STATE stops
    for t in (3, 4, 5.5):
        sim.tick(t)
    board = sim.core.resources.segments
    unknown = [n for n, seg in board.items() if seg.uncertain]
    assert "CROSSROAD" in unknown and sim.core.resources.snapshot()["CORRIDOR"]["state"] == "UNKNOWN"
    name = unknown[-1]
    sim.cmd("RESOURCE_CLEAR_CONFIRM", resource=name)
    out = sim.tick(6)
    ack, = out.acks
    assert ack["ok"], ack
    confirm, = [e for e in out.events if e["type"] == "RESOURCE_CLEAR_CONFIRM"]
    assert confirm["detail"]["resources"] == [name] and confirm["detail"]["by"] == "nurse"
    sim.cmd("RESOURCE_CLEAR_CONFIRM", resource="A1")          # not UNKNOWN: refused
    ack, = sim.tick(6.5).acks
    assert not ack["ok"] and ack["code"] == "E-409"


# ---------------------------------------------------------------- 3. arrival from STATE

def accepted(sim):
    sim.patient(pickup="C", dest="A")
    assert commands(sim.tick(0))[A].startswith("MOVE C2 2")
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(2)
    assert sim.state(A) == "MOVING"


def complete_without_arrived(sim, **changes):
    """The STATUS ARRIVED is lost; only the 1 Hz STATE says COMPLETE."""
    frame = sim.frames[A]
    frame.update(state="COMPLETE", percent=100, pose=sim.pose(sim.cfg.slot_point("C", 2)))
    frame.update(changes)


def test_66_complete_state_alone_reaches_wait_confirm_in_one_tick(sim):
    accepted(sim)
    remote = sim.frames[A]["mission_id"]
    complete_without_arrived(sim)
    out = sim.tick(3)
    assert sim.state(A) == "WAIT_CONFIRM"
    assert "ARRIVED_FROM_STATE" in types(out) and "ARRIVE" in types(out)
    recovered, = [e for e in out.events if e["type"] == "ARRIVED_FROM_STATE"]
    assert recovered["robot"] == A and recovered["detail"]["destination"] == "C"
    assert recovered["detail"]["part4_mission_id"] == remote and recovered["detail"]["slot"] == 2
    assert recovered["detail"]["reason"] == "도착 알림이 빠져 로봇 상태 보고(완료)로 도착을 확인했습니다."
    arrive, = [e for e in out.events if e["type"] == "ARRIVE"]
    assert arrive["detail"]["destination"] == "C" and arrive["detail"]["slot"] == 2
    assert sim.core.mission.robots[A].job.stop_log[0]["status"] == "ARRIVED"


def test_66_late_arrived_after_recovery_is_ignored(sim):
    accepted(sim)
    complete_without_arrived(sim)
    sim.tick(3)
    assert sim.state(A) == "WAIT_CONFIRM"
    sim.event(A, "ARRIVED")  # the lost STATUS shows up late
    out = sim.tick(3.5)
    assert not {"ARRIVE", "ARRIVED_FROM_STATE", "WARNING"} & set(types(out))
    assert sim.state(A) == "WAIT_CONFIRM"
    sim.confirm(A, "p")
    out = sim.tick(4)
    assert commands(out)[A].startswith("MOVE A")  # the next leg goes out normally


@pytest.mark.parametrize("changes", [
    {"mission_id": "other-mission"},
    {"destination": "B"},
    {"slot": 1},
], ids=["other-mission", "other-destination", "other-slot"])
def test_66_complete_for_another_mission_or_destination_is_ignored(sim, changes):
    accepted(sim)
    complete_without_arrived(sim, **changes)
    out = sim.tick(3)
    assert not {"ARRIVE", "ARRIVED_FROM_STATE"} & set(types(out))
    assert sim.state(A) == "MOVING"


def test_66_same_tick_state_then_arrived_counts_once(sim):
    accepted(sim)
    sim.event(A, "ARRIVED")  # the simulator queues STATE COMPLETE before this STATUS
    out = sim.tick(3)
    assert types(out).count("ARRIVE") == 1 and "ARRIVED_FROM_STATE" not in types(out)
    assert sim.state(A) == "WAIT_CONFIRM"


# ---------------------------------------------------------------- 4. NONE does not drive again

def test_67_none_verdict_opens_card_without_automatic_move(sim):
    moving(sim)
    sim.event(A, "MISSION_FAILED", reason="Nav2: ABORTED")
    sim.event(A, "OBSTACLE", destination="C", robot_pose={"x": -1.55, "y": -1.7, "yaw_deg": -92.0},
              captured=["depth", "rgb", "scan"], image_topic="/robot3/hospital_nav/obstacle_image",
              depth_m=0.45, lidar_m=0.42, bearing_deg=2.0, map_x=-1.56, map_y=-2.12)
    sim.tick(3)
    sim.core.enqueue("obstacle_result", json.dumps({"status": "ok", "obstacle_present": False, "detections": []}),
                     3.5, robot=A)
    out = sim.tick(4)
    card, = out.cards
    assert card["kind"] == "BLOCKED" and card["obstacle"]["kind"] == "NONE"
    assert card["reason"] == "의료용 상자는 보이지 않음 — 길이 비었다는 뜻은 아님, 로봇이 찍은 사진과 현장 확인"
    assert sim.core.snapshot(4)["robots"][A]["obstacle"]["text"] == "의료용 상자는 보이지 않음"
    events, moves = [], []
    for t in steps(4, 12):  # well past retry_delay_s (3 s)
        out = sim.tick(t)
        events.extend(e["type"] for e in out.events)
        moves.extend(c.command for c in out.commands if c.robot == A)
    assert not moves and "OBSTACLE_RETRY" not in events
    assert sim.state(A) == "FAULT" and card["card_id"] in sim.core.decisions.cards
    assert sim.cfg.obstacle.retry_if_clear is False  # fleet.yaml default (D-31)
