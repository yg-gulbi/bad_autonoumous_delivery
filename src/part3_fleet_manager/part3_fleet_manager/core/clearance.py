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


def resume_distance(safety, a_still: bool, b_still: bool) -> float:
    """Centre distance from which a proximity stop is let go: the stop distance plus the same margin as
    proximity_resume_m - proximity_stop_m (0.3 m)."""
    return stop_distance(safety, a_still, b_still) + (safety.proximity_resume_m - safety.proximity_stop_m)
