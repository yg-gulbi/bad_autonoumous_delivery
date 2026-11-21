"""Real arbitration, mission wire commands and history on the two field timelines."""
from dataclasses import replace

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"


@pytest.fixture
def sim(cfg, tmp_path):
    sim = Simulation(replace(cfg, db_path=str(tmp_path / "fleet.db")), slots=True)
    yield sim
    sim.core.store.close()


def wire(out):
    return {item.robot: item.command for item in out.commands}


def at(sim, robot, point):
    sim.frames[robot]["pose"] = sim.pose(point)


def count(sim, kind):
    return sim.core.store.db.execute("SELECT count(*) FROM events WHERE type=?", (kind,)).fetchone()[0]


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
    # PLAN (b98f6ca as well): from two positions already closer than proximity_stop_m the plan finds no route
    # (LEGACY sent robot_b back to F here). Both stay still and robot_b says so; nothing drives into the other.
    out = sim.tick(22.5)
    assert not [c for c in out.commands if c.command.startswith(("MOVE", "RESUME"))]
    assert sim.core.resources.held[B]["reason"] == "계획을 찾지 못해 제자리 대기 · 다시 계획을 기다립니다"


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


@pytest.mark.parametrize("peer_state", ["STOPPING_PAUSE", "STOPPING_CANCELED", "UNDOCKING"])
def test_stop_in_progress_is_not_permission_to_depart(sim, peer_state):
    at(sim, B, (-1.76, .8))
    sim.frames[B]["state"] = peer_state
    sim.cmd("GO_ZONE", robot=A, zone="A")
    assert A not in wire(sim.tick(0))
    # The plan holds robot_a for robot_b; the 0.9 m departure check would hold it too (collision.NearStartGuard).
    assert sim.core.wait_reason(A) in ("가까이 움직이는 로봇 대기", "간호 스테이션(D) 1번 자리: 약품 운송 로봇이 지나간 뒤")
    for t in (.5, 1.0, 2.0):                      # still stopping: robot_a stays
        assert A not in wire(sim.tick(t))
