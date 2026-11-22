"""Stage 4O follow-up 4 (20746a2 -> ): a robot back online inside its own segments is settled without the operator
(test_4o_1121_replay.py), and a PAUSED robot may wait right where it stopped (random seed 112)."""
from math import dist

from kinematic_mock import KinematicSim

A, B = "robot_a", "robot_b"


def _until(sim, cond, limit):
    end = sim.now + limit
    while sim.now < end and not cond():
        sim.step(0.2)
    assert cond()


def test_two_robots_paused_at_e1_and_e2_both_leaving_room_e_get_a_plan(cfg):
    """robot_a paused 0.1 m out of E1 on its way to room B, robot_b paused 0.3 m out of E2 on its way to C (1.1 m
    apart). Before, a PAUSED robot had to drive through the whole piece it stood in: the two pieces came within
    0.36 m, so neither had a plan ("계획을 찾지 못해 제자리 대기") and both waited for ever. Now each may wait where
    it stopped: robot_b goes out first (0.69 m from robot_a at the room point), then robot_a."""
    sim = KinematicSim(cfg, poses={A: cfg.slot_point("E", 1), B: cfg.slot_point("E", 2)})
    try:
        sim.run_until(1.0)
        sim.cmd("GO_ZONE", robot=A, zone="B", level=2)
        _until(sim, lambda: dist(sim.robots[A].pose, cfg.slot_point("E", 1)) > 0.1, 20)
        sim.pause(A)
        _until(sim, lambda: sim.robots[A].state == "PAUSED", 5)
        sim.cmd("GO_ZONE", robot=B, zone="C", level=1)
        _until(sim, lambda: dist(sim.robots[B].pose, cfg.slot_point("E", 2)) > 0.3, 20)
        sim.pause(B)
        _until(sim, lambda: sim.robots[B].state == "PAUSED", 5)
        assert 1.0 < dist(sim.robots[A].pose, sim.robots[B].pose) < 1.3
        resumed = sim.now
        sim.resume(A)
        sim.resume(B)
        sim.run_until(sim.now + 90.0)
        failed = [e for e in sim.all_events if e["type"] == "PLAN_WAIT" and e["ts"] >= resumed
                  and e["detail"]["reason"].startswith("계획을 찾지 못해")]
        assert not failed
        assert sim.core.mission.robots[A].zone == "B" and sim.core.mission.robots[B].zone == "C"
        assert not [e for e in sim.all_events if e["type"] == "PROXIMITY_STOP"]
    finally:
        sim.close()
