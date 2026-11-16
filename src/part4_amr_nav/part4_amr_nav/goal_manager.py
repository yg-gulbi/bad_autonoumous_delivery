"""Hospital A-F goal definitions, in the common SLAM map frame.

IMPORTANT: These are temporary TEST GOALS estimated from main_maps.pgm.
Verify every goal in RViz2 and update it to the real room entrance/stop point.
A-F correspond to rooms 1-6 respectively; this mapping is configurable.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GoalPoint:
    x: float
    y: float
    yaw_deg: float = 0.0


# Temporary map-frame goals (metres, degrees).
# Do NOT interpret these as approved entrance points for occupied rooms.
GOALS: dict[str, GoalPoint] = {
    "A": GoalPoint(x=-2.200, y= 0.500, yaw_deg=0.0),  # Room 1
    "B": GoalPoint(x=-2.550, y=-2.200, yaw_deg=0.0),  # Room 2
    "C": GoalPoint(x=-2.300, y=-5.000, yaw_deg=0.0),  # Room 3
    "D": GoalPoint(x=-0.600, y= 0.400, yaw_deg=0.0),  # Room 4
    "E": GoalPoint(x=-0.550, y=-2.400, yaw_deg=0.0),  # Room 5
    "F": GoalPoint(x=-0.800, y=-5.100, yaw_deg=0.0),  # Room 6
}


def get_goal(room: str) -> GoalPoint:
    """Return the position for a one-letter room identifier (A-F)."""
    key = room.strip().upper()
    try:
        return GOALS[key]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 구역 '{room}'. A~F 중 선택하세요.") from exc


def format_goals() -> str:
    """Pretty-print the current target positions."""
    return "\n".join(
        f"  {room} (방 {index}): x={goal.x:+.3f}, y={goal.y:+.3f}, "
        f"yaw={goal.yaw_deg:.0f}°"
        for index, (room, goal) in enumerate(GOALS.items(), start=1)
    )
