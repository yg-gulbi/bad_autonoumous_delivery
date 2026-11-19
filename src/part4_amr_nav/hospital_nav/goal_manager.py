"""A~F room goals in the common map frame (main_maps.yaml).

The coordinates are provisional. Check every goal in RViz2 and update it to
the real stop point in front of each room.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GoalPoint:
    x: float
    y: float
    yaw_deg: float = 0.0


GOALS = {
    "A": GoalPoint(-2.200, 0.500), "B": GoalPoint(-2.550, -2.200),
    "C": GoalPoint(-2.300, -5.000), "D": GoalPoint(-0.600, 0.400),
    "E": GoalPoint(-0.550, -2.400), "F": GoalPoint(-0.800, -5.100),
}


# Robot pose while sitting on its dock = AMCL initial pose at startup.
# robot2: lidar scan matched to the map while docked (faces the dock, east wall).
# robot3 is NOT measured yet.
# Measure: dock the robot, set 2D Pose Estimate, read `ros2 topic echo /robotN/amcl_pose --once`.
DOCKS = {
    "robot2": GoalPoint(-0.330, -5.110, -3.0),  # lidar scan-matched on the dock, 2026-11-17
    "robot3": None,
}
PREDOCK_DISTANCE = 0.6  # m in front of the dock where Nav2 stops before the dock action


def predock(dock: GoalPoint) -> GoalPoint:
    """Point PREDOCK_DISTANCE behind the docked pose, still facing the dock."""
    yaw = math.radians(dock.yaw_deg)
    return GoalPoint(dock.x - PREDOCK_DISTANCE * math.cos(yaw),
                     dock.y - PREDOCK_DISTANCE * math.sin(yaw), dock.yaw_deg)


def get_goal(room: str) -> GoalPoint:
    key = str(room).strip().upper()
    if key not in GOALS:
        raise ValueError(f"알 수 없는 방: {room!r} (A~F)")
    return GOALS[key]


def format_goals() -> str:
    return "\n".join(f"  {k}: ({g.x:+.3f}, {g.y:+.3f}, {g.yaw_deg:.0f}°)"
                     for k, g in GOALS.items())
