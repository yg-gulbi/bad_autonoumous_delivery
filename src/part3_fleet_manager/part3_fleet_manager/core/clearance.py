"""Proximity distances, one definition (4O 정리) for the plan (``plan_graph.PlanGraph.threshold``: which positions
conflict) and for the proximity stop (``collision.CollisionControl``: when a driving robot stops and when it goes
on). Before, the two were written apart, and once they disagreed: a robot passing another standing at a slot at
0.45~0.6 m (allowed by the plan) was stopped by the proximity rule when the standing robot's link dropped, and was
not let go again until 0.9 m (random seeds 54 and 248 of 4O follow-up 5).

"Standing still at a slot" (``still``) is the same fact on both sides: no granted leg, stopped (STATE done or
PAUSED), at a slot, whether its link is up or not (robot_state.RobotStates.standing_still; for the plan, a
standing position at a slot of a robot without a granted leg)."""


def stop_distance(safety, a_still: bool, b_still: bool) -> float:
    """Centre distance below which a driving robot stops: proximity_stop_m, or slot_stop_m when either robot
    stands still at a slot (it will not come toward the other one)."""
    if a_still or b_still:
        return min(safety.proximity_stop_m, safety.slot_stop_m)
    return safety.proximity_stop_m


def conflict_distance(safety, a_standing: bool, b_standing: bool, a_still: bool, b_still: bool) -> float | None:
    """Centre distance below which two positions conflict (the plan) / a driving robot stops: None when both stand
    (no stop can occur; two robots parked in their own slots, C1·C2 0.55 m apart, are not a conflict, 4L), else
    ``stop_distance`` (slot_stop_m between a driving robot and one standing still at a slot)."""
    if a_standing and b_standing:
        return None
    return stop_distance(safety, a_still, b_still)


def close_distance(safety, a_parked: bool, b_parked: bool) -> float:
    """Two standing robots closer than this are already inside the stop distance that applies once either of them
    starts (the other one standing): the start the ``leaving`` rule is for. ``*_parked``: standing in its own slot
    (the one its MOVE took it to), whether or not it has a granted leg now. Both parked: slot_stop_m (parked in
    their own slots, C1·C2 0.55 m, they are not close, 4L); else proximity_stop_m."""
    return max(stop_distance(safety, False, b_parked), stop_distance(safety, a_parked, False))


# Two robots that are already closer than the stop distance (they stopped there, or were put there) may move apart:
# a move that does not bring them closer than they are is not a conflict until they are apart (planner ``grace``,
# plan_exec gate, collision.observe_distance). This much closer still counts as "not closer" (pose rounding);
# a real approach (1 cm, test_4n) is not.
LEAVING_SLACK_M = 0.005


def leaving(start_m: float, now_m: float) -> bool:
    """Robots that started ``start_m`` apart (inside the stop distance) and are ``now_m`` apart are not closing in."""
    return now_m >= start_m - LEAVING_SLACK_M


def resume_distance(safety, a_still: bool, b_still: bool) -> float:
    """Centre distance from which a proximity stop is let go: the stop distance plus the same margin as
    proximity_resume_m - proximity_stop_m (0.3 m)."""
    return stop_distance(safety, a_still, b_still) + (safety.proximity_resume_m - safety.proximity_stop_m)
