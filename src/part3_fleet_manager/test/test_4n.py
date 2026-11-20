"""Real arbitration, mission wire commands and history on the two field timelines."""
from dataclasses import replace

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"


@pytest.fixture(params=["LEGACY", "SEGMENTS"])
def sim(cfg, tmp_path, request):
    sc = replace(cfg, db_path=str(tmp_path / f"fleet-{request.param}.db"),
                 traffic=replace(cfg.traffic, mode=request.param))
    sim = Simulation(sc, slots=True)
    yield sim
    sim.core.store.close()


def wire(out):
    return {item.robot: item.command for item in out.commands}


def at(sim, robot, point):
    sim.frames[robot]["pose"] = sim.pose(point)


def count(sim, kind):
    return sim.core.store.db.execute("SELECT count(*) FROM events WHERE type=?", (kind,)).fetchone()[0]


def test_150226_wait_at_exit_until_moving_robot_is_clear(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 문구·시점 (SEGMENTS 검증은 test_4o_1120_replay.py에서 수행)")
    # B is waiting outside the lower mouth. A clears the corridor, passing the parked B.
    at(sim, B, sim.cfg.slot_point("F", 2))
    sim.cmd("GO_ZONE", robot=A, zone="C")
    sim.tick(0)
    assert wire(sim.tick(1))[A].startswith("MOVE C ")
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-1.65, -3.7))
    sim.cmd("GO_ZONE", robot=B, zone="D")
    sim.tick(1.1)
    at(sim, A, (-1.81, -4.70))
    sim.tick(2)
    for i in range(1, 12):
        at(sim, A, (-1.81 - .039*i/11, -4.7))
        assert not sim.tick(2 + .1*i).commands
    assert A not in sim.core.resources.snapshot()["CORRIDOR"]["holders"]
    assert sim.core.wait_reason(B) == "가까이 움직이는 로봇 대기"  # 0.499 m
    at(sim, A, (-2.26, -4.70))  # 0.91 m and still moving
    assert B not in wire(sim.tick(3.2))  # remaining corridor decision window
    assert wire(sim.tick(4))[B].startswith("MOVE D ")
    assert count(sim, "PROXIMITY_STOP") == 0


def test_150338_wait_for_c2_arrival_then_for_its_exit(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 문구·EXIT 카드 (SEGMENTS 검증은 test_4o_1120_replay.py에서 수행)")
    at(sim, B, "C")
    sim.patient("bed", "C", "F")
    sim.tick(0)
    assert wire(sim.tick(1))[A].startswith("MOVE C2 ")
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-1.65, -3.55))
    sim.cmd("GO_ZONE", robot=B, zone="D")
    assert not sim.tick(2).commands
    assert sim.core.wait_reason(B) == "수술실(C)에 들어오는 로봇 대기"
    at(sim, A, (-1.85, -4.52))
    assert not sim.tick(3).commands
    assert not sim.tick(4.1).commands  # corridor released before A has parked
    at(sim, A, (-2.758, -4.539))  # exactly 0.584 m from C1 while still approaching C2
    assert not sim.tick(4.15).commands
    assert sim.core.wait_reason(B) == "수술실(C)에 들어오는 로봇 대기"
    sim.event(A, "ARRIVED")
    arrival = sim.tick(4.2)
    assert not arrival.commands
    assert sim.core.wait_reason(B).startswith("출구 막힘 대기 · 수술실(C) 2번 자리")
    assert next(e for e in arrival.events if e["type"] == "ARRIVE")["detail"]["reason"] == "수술실(C) 2번 자리 도착"
    assert sim.core.store.db.execute("SELECT zone FROM mission_stops ORDER BY seq LIMIT 1").fetchone()[0] == "C"
    card, = sim.tick(34.3).cards
    assert card["context"] == "EXIT" and "수술실(C) 2번 자리" in card["reason"]
    sim.confirm(A, "bed")
    assert wire(sim.tick(35))[A].startswith("MOVE F ")
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-2.15, -4.64))
    assert not sim.tick(36).commands  # past C2, still within 0.9 m of B
    at(sim, A, (-1.70, -4.62))
    assert wire(sim.tick(37))[B].startswith("MOVE D ")
    assert count(sim, "PROXIMITY_STOP") == 0


def test_arrival_pauses_outside_the_radius_when_departure_was_first(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 2.0 m 반경 규칙 (SEGMENTS에서는 비활성)")
    at(sim, B, "C")
    sim.cmd("GO_ZONE", robot=B, zone="F")  # actually leaves C before the arrival
    assert wire(sim.tick(0))[B].startswith("MOVE F ")
    sim.cmd("GO_ZONE", robot=A, zone="C")
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(.1)
    assert wire(sim.tick(1.1))[A].startswith("MOVE C")
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-1.7, -2.8))  # just outside C2's 2 m boundary
    assert sim.core.config.resources.zone_arrival_clear_m == 2.0
    assert wire(sim.tick(2)) == {A: "PAUSE"}
    assert sim.core.collision.access.distance(A, "C") > 2
    assert sim.core.wait_reason(A) == "수술실(C)에서 나오는 로봇 대기"
    sim.event(A, "PAUSED")
    assert not sim.tick(3).commands
    at(sim, B, (-1.0, -2.2))
    assert wire(sim.tick(4)) == {A: "RESUME"}
    assert count(sim, "PROXIMITY_STOP") == 0


def test_receding_then_closing_stops_once_and_never_clears_on_trend_alone(sim):
    # The last half second of fresh pose pairs already shows separation when MOVING arrives.
    for i in range(7):
        at(sim, A, (-1.4, -2.0))
        at(sim, B, (-1.4, -2.38 - .02*i))
        sim.tick(i/10)
    sim.frames[B]["state"] = "MOVING"
    at(sim, B, (-1.4, -2.52))
    assert not sim.tick(.7).commands
    at(sim, B, (-1.4, -2.51))
    assert wire(sim.tick(.8)) == {B: "PAUSE"}
    sim.frames[B]["state"] = "PAUSED"
    for i in range(11):
        at(sim, B, (-1.4, -2.53 - .02*i))
        assert not sim.tick(.9 + i/10).commands
    assert count(sim, "PROXIMITY_STOP") == 1
    assert count(sim, "PROXIMITY_CLEAR") == 0


@pytest.mark.parametrize("choice", ["self_first", "peer_first"])
def test_failed_priority_leaves_one_card_and_later_mover_reverses(sim, choice):
    sim.cmd("GO_ZONE", robot=A, zone="A")
    assert wire(sim.tick(0))[A].startswith("MOVE A ")
    sim.event(A, "MISSION_ACCEPTED")
    sim.cmd("GO_ZONE", robot=B, zone="C")
    assert wire(sim.tick(1))[B].startswith("MOVE C ")
    sim.event(B, "MISSION_ACCEPTED")
    at(sim, A, (-1.7, -4.6))
    at(sim, B, (-2.22, -4.6))
    assert wire(sim.tick(2)) == {A:"PAUSE", B:"PAUSE"}
    sim.event(A, "PAUSED")
    sim.event(B, "PAUSED")
    sim.tick(3)
    cards = sim.tick(12).cards
    card = next(c for c in cards if c["robots"] == [B if choice == "self_first" else A])
    sim.choose(card, choice)
    assert wire(sim.tick(12.1)) == {B:"RESUME"}
    sim.event(B, "RESUMED")
    at(sim, B, (-2.21, -4.6))
    assert wire(sim.tick(12.2)) == {B:"PAUSE"}
    sim.event(B, "PAUSED")
    sim.tick(12.3)
    sim.tick(22.3)
    card, = sim.core.decisions.cards.values()
    assert card["head_on"] and card["robots"] == [B] and card["recommended"] == "return"
    assert card["reason"] == "두 로봇이 마주 보고 있어 한쪽이 물러나야 합니다"
    assert [o["id"] for o in card["options"]] == ["return","hold"] and card["default"] is None
    sim.choose(card, "return")
    assert wire(sim.tick(22.4)) == {B:"CANCEL"}
    sim.event(B, "MISSION_CANCELED")
    assert wire(sim.tick(22.5))[B].startswith("MOVE F ")
    sim.event(B, "MISSION_ACCEPTED")
    at(sim, B, (-2.4, -4.6))
    assert not sim.tick(22.6).commands
    at(sim, B, (-2.65, -4.6))
    assert wire(sim.tick(22.7)) == {A:"RESUME"}
    assert not sim.core.collision.head_on


def test_exit_warning_is_advisory_and_uses_finite_segments(cfg):
    assert cfg.exit_clearance("C") == pytest.approx(.398, abs=.001)
    assert cfg.exit_warnings() == ["간호 스테이션(D) 출구 경로와 2번 자리 간격 0.49 m (기준 0.55 m 미만). slot2_blocks_exit 설정을 확인하세요."]
    override = replace(cfg, exit_blocked_zones=cfg.exit_blocked_zones - {"C"})
    assert any("수술실(C)" in line for line in override.exit_warnings())
    assert "C" not in override.exit_blocked_zones


def test_unacknowledged_move_retry_rechecks_nearby_moving_peer(sim):
    # The first command was lost. Its retry must not start the robot beside a moving peer.
    sim.cmd("GO_ZONE", robot=A, zone="A")
    assert wire(sim.tick(0))[A].startswith("MOVE A ")
    at(sim, B, (-1.78, .80))
    sim.frames[B]["state"] = "MOVING"
    assert not sim.tick(3).commands
    assert sim.core.wait_reason(A) == "가까이 움직이는 로봇 대기"
    at(sim, B, (-2.1, .80))
    assert wire(sim.tick(3.1))[A].startswith("MOVE A ")


@pytest.mark.parametrize("last_time,last_distance", [(1.5, .53), (.7, .50)])
def test_gap_in_observations_or_flat_latest_reading_does_not_waive_stop(sim, last_time, last_distance):
    for i in range(7):
        at(sim, A, (-1.4, -2.0))
        at(sim, B, (-1.4, -2.38 - .02*i))
        sim.tick(i/10)
    sim.frames[B]["state"] = "MOVING"
    at(sim, B, (-1.4, -2.0 - last_distance))
    assert wire(sim.tick(last_time)) == {B: "PAUSE"}


def test_outbound_arrival_at_neighbouring_zone_is_not_proof_of_clearance(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 2.0 m 반경 규칙 (SEGMENTS에서는 비활성)")
    at(sim, B, "C")
    sim.cmd("GO_ZONE", robot=B, zone="F")
    assert wire(sim.tick(0))[B].startswith("MOVE F ")
    sim.cmd("GO_ZONE", robot=A, zone="C")
    sim.event(B, "MISSION_ACCEPTED")
    sim.tick(.1)
    sim.tick(1.1)
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-1.7, -2.8))
    assert wire(sim.tick(2)) == {A: "PAUSE"}
    sim.event(A, "PAUSED")
    sim.event(B, "ARRIVED")  # F1 is within 2 m of C1
    assert A not in wire(sim.tick(3))
    assert sim.core.wait_reason(A) == "수술실(C)에서 나오는 로봇 대기"
    at(sim, B, "D")
    assert wire(sim.tick(4)) == {A: "RESUME"}


def test_simultaneous_swap_in_overlapping_radii_has_one_wire_departure(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 2.0 m 반경·INBOUND 문구 (SEGMENTS에서는 출구 칸 1순위 규칙으로 대체)")
    # D and A are in the same hall. Both starts are inside the other's 2 m radius.
    at(sim, B, "A")
    sim.cmd("GO_ZONE", robot=A, zone="A")
    sim.cmd("GO_ZONE", robot=B, zone="D")
    sent = wire(sim.tick(0))
    assert list(sent) == [A] and sent[A].startswith("MOVE A2 ")
    sim.event(A, "MISSION_ACCEPTED")
    assert not sim.tick(.1).commands
    assert sim.core.wait_reason(B) == "응급실(A)에 들어오는 로봇 대기"
    assert sim.core.wait_reason(A) is None
    sim.event(A, "ARRIVED")
    assert not sim.tick(.2).commands
    assert sim.core.wait_reason(B).startswith("출구 막힘 대기")


@pytest.mark.parametrize("peer_state", ["STOPPING_PAUSE", "STOPPING_CANCELED", "UNDOCKING"])
def test_stop_in_progress_is_not_permission_to_depart(sim, peer_state):
    at(sim, B, (-1.76, .8))
    sim.frames[B]["state"] = peer_state
    sim.cmd("GO_ZONE", robot=A, zone="A")
    assert A not in wire(sim.tick(0))
    assert sim.core.wait_reason(A) == "가까이 움직이는 로봇 대기"
    sim.frames[B]["state"] = "PAUSED"
    assert wire(sim.tick(.1))[A].startswith("MOVE A ")


def test_arrival_status_before_parked_pose_keeps_outgoing_robot_in_place(sim):
    if sim.cfg.traffic.mode == "SEGMENTS":
        pytest.skip("LEGACY 전용 ZoneTraffic 문구·출구 막힘 대기 (SEGMENTS에서는 slot 칸으로 대체)")
    at(sim, B, "C")
    sim.patient("bed", "C", "F")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    at(sim, A, (-1.75, -4.42))  # outside the corridor, not at either C slot yet
    sim.cmd("GO_ZONE", robot=B, zone="D")
    sim.tick(2)
    assert sim.core.wait_reason(B) == "수술실(C)에 들어오는 로봇 대기"
    sim.frames[A].update(state="COMPLETE", percent=100)  # recovery from terminal STATE, no ARRIVED pose
    assert B not in wire(sim.tick(3.2))
    assert sim.core.wait_reason(B) == "수술실(C)에 들어오는 로봇 대기"
    at(sim, A, sim.cfg.slot_point("C", 2))
    assert B not in wire(sim.tick(3.3))
    assert sim.core.wait_reason(B).startswith("출구 막힘 대기")
