"""Stage 4O follow-up 6 (D-67, D-68): the operator's answer to a BLOCKED card reaches the plan as one of two inputs
(the block is lifted, or a new goal), and two robots already inside the 0.6 m proximity stop distance get apart by
themselves (a move that does not bring them closer is allowed until they are apart).

Scenes in the kinematic mock (simulated motion, the same PLAN as the node). The scripted-frame versions are
test_fleet_core.py::test_blocked_choices_respect_protocol_state, test_4j_obstacle_report.py::test_57_* and
test_4n.py::test_robots_stopped_inside_the_stop_distance_move_apart_by_themselves."""
from math import dist

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core.clearance import LEAVING_SLACK_M

A, B = "robot_a", "robot_b"


def _until(sim, cond, seconds, dt=0.2):
    end = sim.now + seconds
    while sim.now < end - 1e-9:
        if cond():
            return
        sim.step(dt)
    assert cond(), f"not reached in {seconds} s (t={sim.now:.1f})"


def _events(sim, kind, since=0.0, robot=None):
    return [e for e in sim.all_events if e["type"] == kind and e["ts"] >= since and (robot is None or e["robot"] == robot)]


def _idle(sim):
    return all(u.job is None and not u.queue for u in sim.core.mission.robots.values())


# --- 1. face to face inside 0.6 m -------------------------------------------------------------------------------

FACE_OFFS = {
    # robot_a in the lower corridor heading up to C, robot_b below it heading down to A (one way, one lane).
    "corridor": ((-1.55, -2.9), (0, -1), ("C", "A")),
    # robot_a in the hall heading to A, robot_b west of it heading to F (they must pass each other).
    "hall": ((-1.7, -4.6), (-1, 0), ("A", "F")),
}


@pytest.mark.parametrize("gap", [0.42, 0.55])
@pytest.mark.parametrize("place", sorted(FACE_OFFS))
def test_face_off_inside_the_stop_distance_gets_apart_without_a_card(cfg, place, gap):
    """Both standing, 0.42 / 0.55 m apart, each sent past the other. Before: the start was already a conflict, the
    plan found no way, and [되돌아가기] left both standing. Now one of them moves away first, the other follows once
    they are apart; nobody gets closer than they started, no proximity stop, no card, both arrive."""
    (ax, ay), (dx, dy), (za, zb) = FACE_OFFS[place]
    poses = {A: (ax, ay), B: (ax + dx * gap, ay + dy * gap)}
    sim = KinematicSim(cfg, poses=poses)
    try:
        sim.cmd("GO_ZONE", robot=A, zone=za, level=1)
        sim.cmd("GO_ZONE", robot=B, zone=zb, level=1)
        closest, apart_at = dist(*poses.values()), None
        while sim.now < 120.0 and not (sim.now > 1.0 and _idle(sim)):
            sim.step(0.2)
            d = dist(sim.robots[A].pose, sim.robots[B].pose)
            closest = min(closest, d)
            if apart_at is None and d >= cfg.safety.proximity_stop_m:
                apart_at = sim.now
        assert _idle(sim), f"not done in 120 s: {sim.core.wait_reason(A)} / {sim.core.wait_reason(B)}"
        assert sim.core.mission.robots[A].zone == za and sim.core.mission.robots[B].zone == zb
        assert apart_at is not None and apart_at <= 10.0
        assert closest >= gap - LEAVING_SLACK_M - 0.01          # one pose step of rounding
        assert not _events(sim, "PROXIMITY_STOP")
        assert not _events(sim, "CARD_OPEN")
        assert not any(e["detail"]["fallback"] for e in _events(sim, "PLAN_UPDATE"))
    finally:
        sim.close()


def test_face_off_with_one_robot_standing_only_the_other_moves_away(cfg):
    """robot_b stands (no mission) 0.5 m below robot_a in the corridor; robot_a is sent away from it (up to D):
    it may go at once. robot_b is then sent toward where robot_a was: it waits until they are apart."""
    sim = KinematicSim(cfg, poses={A: (-1.55, -2.9), B: (-1.55, -3.4)})
    try:
        sim.cmd("GO_ZONE", robot=A, zone="D", level=1)
        _until(sim, lambda: any(r == A and c.startswith("MOVE") for _, r, c in sim.all_commands), 2.0)
        _until(sim, lambda: dist(sim.robots[A].pose, sim.robots[B].pose) >= cfg.safety.proximity_stop_m, 10.0)
        sim.cmd("GO_ZONE", robot=B, zone="C", level=1)
        _until(sim, lambda: _idle(sim) and sim.now > 3.0, 120.0)
        assert sim.core.mission.robots[A].zone == "D" and sim.core.mission.robots[B].zone == "C"
        assert not _events(sim, "PROXIMITY_STOP")
    finally:
        sim.close()


# --- 2. BLOCKED card: [다시 출발] lifts the block, [되돌아가기] is a new goal ----------------------------------------

def _box_card(sim):
    """robot_b E -> B through the corridor; a medical box in front of it in the corridor; Nav2 gives up, PART 06
    says medical_box, the BLOCKED card opens."""
    core = sim.core
    sim.cmd("GO_ZONE", robot=B, zone="E", level=1)
    _until(sim, lambda: core.mission.robots[B].zone == "E" and core.mission.robots[B].job is None, 90)
    sim.cmd("GO_ZONE", robot=B, zone="B", level=1)
    _until(sim, lambda: sim.robots[B].state == "MOVING" and core.config.corridor.region_of(sim.robots[B].pose)
           == "CORRIDOR", 60)
    put = sim.now
    assert sim.obstacle(B)
    _until(sim, lambda: _events(sim, "OBSTACLE_REPORT", put, B), 10)
    sim.obstacle_verdict(B, "medical_box")
    _until(sim, lambda: [c for c in core.decisions.cards.values() if c["kind"] == "BLOCKED" and B in c["robots"]], 3)
    card, = [c for c in core.decisions.cards.values() if c["kind"] == "BLOCKED" and B in c["robots"]]
    assert core.resources.snapshot()["CORRIDOR"]["blocked"]
    return card


@pytest.mark.parametrize("choice", ["retry", "return"])
def test_box_card_answer_gets_the_robot_going(cfg, choice):
    """[치웠음, 다시 출발]: the same leg is planned again (MOVE within a second) and robot_b arrives at B.
    [되돌아가기]: a new leg to the zone it came from (E); there it waits for the original leg to be retried (FAULT,
    as before), and RETRY takes it to B. In both cases the robot is in the plan again at once (MOVE within a second
    of the answer); while the block holds the plan does not let it go."""
    sim = KinematicSim(cfg)
    core = sim.core
    try:
        card = _box_card(sim)
        sim.run_until(sim.now + 2.0)
        assert B not in core.resources.go                       # blocked: the plan does not let it go
        chose = sim.now
        sim.choose(card["card_id"], choice)
        _until(sim, lambda: any(t > chose and r == B and c.startswith("MOVE") for t, r, c in sim.all_commands), 1.0)
        assert card["card_id"] not in core.decisions.cards
        if choice == "return":
            _until(sim, lambda: core.mission.robots[B].zone == "E" and core.mission.robots[B].state == "FAULT", 60)
            assert core.mission.robots[B].job is not None             # the original leg (E -> B) is still to do
            sim.cmd("RETRY", robot=B)
        _until(sim, lambda: core.mission.robots[B].zone == "B" and core.mission.robots[B].job is None, 90)
        assert _events(sim, "UNBLOCKED", chose, B)
        assert not core.blocks and not core.block_legs
        assert not _events(sim, "PROXIMITY_STOP")
    finally:
        sim.close()


# --- 3. the 4L rule in one place (clearance) -------------------------------------------------------------------

def test_clearance_keeps_the_4l_slot_rule(cfg):
    """Two robots parked in their own slots are no conflict (C1·C2 are 0.55 m apart, closer than proximity_stop_m);
    between a robot passing and one standing still at a slot it is slot_stop_m (0.45); otherwise proximity_stop_m.
    ``close_distance`` (already inside the stop distance at the start) follows the same rule."""
    from part3_fleet_manager.core.clearance import close_distance, conflict_distance, stop_distance
    safety = cfg.safety
    c1, c2 = cfg.slot_point("C", 1), cfg.slot_point("C", 2)
    assert dist(c1, c2) == pytest.approx(0.55, abs=0.01) and dist(c1, c2) < safety.proximity_stop_m
    assert conflict_distance(safety, True, True, True, True) is None            # both parked: no conflict
    assert conflict_distance(safety, False, True, False, True) == safety.slot_stop_m == 0.45
    assert conflict_distance(safety, False, False, False, False) == safety.proximity_stop_m
    assert stop_distance(safety, False, True) == 0.45
    assert dist(c1, c2) >= close_distance(safety, True, True) == 0.45          # parked in C1 and C2: not close
    assert close_distance(safety, True, False) == close_distance(safety, False, False) == 0.6


@pytest.mark.parametrize("off", [0.0, 0.10, 0.16, 0.20])
def test_robots_parked_off_the_points_of_c1_and_c2_are_not_close(cfg, off):
    """A real robot stops some way off the slot point. Parked in C1 and C2 (inside the circles) and 0.50~0.55 m apart,
    the plan, the executor and collision.close_start all take the pair as not close (4L), and robot_b leaves C2."""
    c1, c2 = cfg.slot_point("C", 1), cfg.slot_point("C", 2)
    sim = KinematicSim(cfg, poses={A: (c1[0], c1[1] + off / 2), B: (c2[0] + off, c2[1])})
    try:
        pm = sim.core.resources
        assert not sim.core.collision.close_start                # settled at start-up: C1·C2 are not close
        sim.run_until(0.6)
        gap = dist(sim.robots[A].pose, sim.robots[B].pose)
        assert cfg.safety.slot_stop_m < gap < cfg.safety.proximity_stop_m
        start = [pm.planner.piece_of(pm.planner.initial_state(a)) for a in pm.make_job(sim.now, "test").agents]
        assert not pm.graph.close(*start)
        assert not pm._too_close(A) and not pm._too_close(B)
        assert not sim.core.collision.close_start
        sim.cmd("GO_ZONE", robot=B, zone="F", level=1)
        _until(sim, lambda: sim.core.mission.robots[B].zone == "F" and sim.core.mission.robots[B].job is None, 60)
        assert not _events(sim, "PROXIMITY_STOP")
    finally:
        sim.close()


def test_arrival_into_c2_beside_c1_does_not_mark_close_start(cfg):
    """robot_a stands parked in C1 while robot_b drives into C2 (0.55 m apart): on the arrival tick and after,
    collision.close_start does not mark the pair as close (4L)."""
    sim = KinematicSim(cfg, poses={A: cfg.slot_point("C", 1), B: cfg.slot_point("F", 1)})
    try:
        sim.cmd("GO_ZONE", robot=B, zone="C", level=1)
        _until(sim, lambda: sim.core.mission.robots[B].zone == "C" and sim.core.mission.robots[B].job is None, 60)
        assert sim.core.resources.parked_at[A] == "C1" and sim.core.resources.parked_at[B] == "C2"
        assert dist(sim.robots[A].pose, sim.robots[B].pose) == pytest.approx(0.55, abs=0.01)
        assert not sim.core.collision.close_start
        sim.run_until(sim.now + 1.0)
        assert not sim.core.collision.close_start
        assert not _events(sim, "PROXIMITY_STOP")
    finally:
        sim.close()


def test_robots_parked_in_c1_and_c2_are_not_close_in_the_plan(cfg):
    """The plan graph: the parked positions of C1 and C2 neither conflict nor count as a close start (no grace);
    a free standing position (a robot that stopped on the way, not parked) 0.55 m from a parked one does."""
    sim = KinematicSim(cfg)
    try:
        g = sim.core.resources.graph
        c1, c2 = g.park["C1"], g.park["C2"]
        assert not g.conflict(c1, c2) and not g.close(c1, c2)
        free = g.stand((cfg.slot_point("C", 2)[0] + 0.02, cfg.slot_point("C", 2)[1]))   # not the slot point
        assert g.close(c1, free)
    finally:
        sim.close()


# --- 4. random seeds fixed as named tests ----------------------------------------------------------------------

from test_4o_random import run_random_seed  # noqa: E402


@pytest.mark.parametrize("seed", [50, 62, 161, 213])
def test_seeds_50_62_161_213_neighbour_slots_do_not_wait_for_each_other(cfg, seed):
    """While 4O 후속 6 was half done, two robots at neighbouring slots (A1·A2, C1·C2, F1·F2) waited for each other for
    over 30 s (Inv3): the executor held one for the other to "move apart first" although the plan's order had that
    one wait. Without the order check in plan_exec._leaves_next 62, 161 and 213 fail again (a fixed 0.6 m
    close_distance does not bring them back); 50 fails with the same A1·A2 cycle once plan_graph.close is reverted
    so the run still reaches that A1·A2 scene (t=578~620 s) — keeping the order check alone passes it."""
    stats = run_random_seed(cfg, seed)
    assert stats["prox_stops_planned"] == 0


@pytest.mark.parametrize("seed", [101, 198, 205, 272])
def test_seeds_found_by_follow_up_6(cfg, seed):
    """Seeds 101·272: a robot driving past one standing at a slot (0.45~0.6 m) is no close start (the standing one
    got its leg too early, proximity stop). 198: two robots standing 0.61 m apart in CORRIDOR_BOTTOM held each other
    up (one_way segment without direction). 205: an UNKNOWN segment's fence and a robot whose link was lost while
    driving are no standing robots for the close start."""
    stats = run_random_seed(cfg, seed)
    assert stats["prox_stops_planned"] == 0
    if seed in (101, 205, 272):
        assert stats["prox_stops"] == 0
