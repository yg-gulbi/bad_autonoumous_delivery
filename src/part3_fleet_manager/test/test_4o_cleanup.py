"""4O 정리: one traffic judgement (PLAN), the robot state layer, conflict kinds in one table, one proximity distance
rule, and the plan's search outside the tick (plan_runner)."""
from dataclasses import dataclass, replace
import time

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core import plan_exec
from part3_fleet_manager.core.fleet_core import FleetCore
from part3_fleet_manager.core.plan_exec import CROSS, FOLLOW, MEET, ORDER_KEPT, conflict_kind
from test_4o_random import run_random_seed

A, B = "robot_a", "robot_b"


@dataclass
class _E:
    point: str
    standing: bool = False


def _way(*points):
    return [_E(p) for p in points]


# --- conflict kinds and ORDER_KEPT ----------------------------------------------------------------------------

def test_conflict_kinds_follow_meet_cross():
    top = [f"CORRIDOR_TOP_{k}" for k in (1, 2, 3)]
    assert conflict_kind(_way(top[1], top[2]), _way(top[0], top[1], top[2])) == FOLLOW
    assert conflict_kind(_way(top[1], top[0]), _way(top[0], top[1], top[2])) == MEET
    assert conflict_kind(_way("CROSSROAD", "CORRIDOR_BOTTOM_1"), _way(top[2], "CROSSROAD", "DOOR_E")) == CROSS


def test_order_kept_table_has_every_kind_and_follower_state():
    states = {plan_exec.DRIVING, plan_exec.WAITING_HERE, plan_exec.WAITING_BEFORE}
    assert set(ORDER_KEPT) == {CROSS, MEET, FOLLOW}
    for kind, rows in ORDER_KEPT.items():
        assert set(rows) == states
        for alternatives in rows.values():
            assert all(name in plan_exec.PlanExec._CONDITIONS for row in alternatives for name in row)
            assert alternatives[0] == ("ahead_left_shared", "ahead_passed")      # passed always keeps the order
    assert all(len(ORDER_KEPT[k][s]) == 1 for k in (CROSS, MEET) for s in states)  # only passing, nothing else
    follow = ORDER_KEPT[FOLLOW]
    assert "gap_follow" in follow[plan_exec.DRIVING][1] and "gap_resume" in follow[plan_exec.WAITING_HERE][1]
    assert "ahead_not_in_single" in follow[plan_exec.WAITING_HERE][1]
    assert "ahead_not_in_single" not in follow[plan_exec.DRIVING][1]


# --- the robot state layer --------------------------------------------------------------------------------------

def test_robot_state_layer_tells_the_plan_what_a_robot_does(cfg):
    sim = KinematicSim(cfg)
    try:
        robots = sim.core.resources.robots
        view = robots.view(A)
        assert view.connected and not view.driving and view.still and robots.parked_at[A] == "D1"
        sim.cmd("GO_ZONE", robot=A, zone="C", level=1)
        while not robots.view(A).rolling:
            sim.step(0.2)
        view = robots.view(A)
        assert view.driving and view.moving and robots.move_endpoint(A) in ("C1", "C2")
        sim.drop_link(A, 12.0)
        sim.run_until(sim.now + 10.0)
        view = robots.view(A)
        assert not view.connected and view.coasting and A in robots.lost
        sim.run_until(sim.now + 4.0)
        assert robots.view(A).connected and A not in robots.lost
    finally:
        sim.close()


# --- the plan's search outside the tick -------------------------------------------------------------------------

def test_plan_result_is_used_in_the_next_tick(cfg):
    sim = KinematicSim(cfg)
    try:
        runner = sim.core.resources.runner
        sim.cmd("GO_ZONE", robot=A, zone="C", level=1)
        out = sim.step(0.2)
        assert runner.busy and not [c for c in out.commands if c.robot == A]
        out = sim.step(0.2)
        assert not runner.busy and [c.command for c in out.commands if c.robot == A][0].startswith("MOVE C")
    finally:
        sim.close()


def test_result_made_before_a_later_change_is_dropped(cfg):
    sim = KinematicSim(cfg)
    try:
        pm = sim.core.resources
        sim.cmd("GO_ZONE", robot=A, zone="C", level=1)
        sim.step(0.2)
        assert pm.runner.busy
        dropped = pm.runner.dropped
        pm._mark("시험: 그사이 바뀜")
        out = sim.step(0.2)
        assert pm.runner.dropped == dropped + 1 and pm.runner.busy       # planned again from now
        assert not [c for c in out.commands if c.robot == A]
        out = sim.step(0.2)
        assert [c.command for c in out.commands if c.robot == A][0].startswith("MOVE C")
    finally:
        sim.close()


def test_a_slow_search_does_not_hold_the_tick_up(cfg):
    """The node runs the search in its own thread: a 0.3 s search leaves the tick (and the proximity stop in it)
    at its own pace; the result comes in a later tick."""
    core = FleetCore(replace(cfg, db_path=":memory:"), plan_thread=True)
    try:
        pm = core.resources
        plan = pm.planner.plan

        def slow(*args, **kwargs):
            time.sleep(0.3)
            return plan(*args, **kwargs)

        pm.planner.plan = slow
        started = time.perf_counter()
        core.tick(0.0)
        assert pm.runner.busy and time.perf_counter() - started < 0.1
        ticks = 0
        while pm.runner.busy and ticks < 100:
            ticks += 1
            t0 = time.perf_counter()
            core.tick(ticks * 0.1)
            assert time.perf_counter() - t0 < 0.1
            time.sleep(0.01)
        assert not pm.runner.busy and ticks >= 2 and pm.plan_info["reason"] == "시작"
    finally:
        core.traffic.close()
        core.store.close()


# --- random seeds the cleanup brought out ----------------------------------------------------------------------

@pytest.mark.parametrize("seed", [34, 173])
def test_seeds_34_173_a_passed_segment_is_not_granted_again(cfg, seed):
    """With the plan's result one tick later, a robot that had passed a corridor segment in the piece it drove got
    it back as GRANTED until the next plan (Inv5 at the end of the run). Only the rest of the piece is granted."""
    run_random_seed(cfg, seed)
