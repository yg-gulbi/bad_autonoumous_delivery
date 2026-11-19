"""A~F room goals in the common map frame (main_maps.yaml).

Every room has two stop points (slots). The first robot sent to a room gets
slot 1 (the inner one), a second robot sent to the same room gets slot 2
(nearer the entrance), so it never has to squeeze past the first. Both report
ARRIVED for the room. Picked on the map 2026-11-19, all >= 0.40 m from walls;
F was moved 0.65 m west so a robot parked at F does not block robot2's dock.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GoalPoint:
    x: float
    y: float
    yaw_deg: float = 0.0


SLOTS = {  # room: (slot 1 = inner, slot 2 = entrance side)
    "A": (GoalPoint(-2.383, 1.027), GoalPoint(-2.458, 0.282)),
    "B": (GoalPoint(-2.500, -1.250), GoalPoint(-2.600, -2.800)),  # inside, a bit past the door
    "C": (GoalPoint(-2.758, -5.123), GoalPoint(-2.738, -4.573)),
    # D: kept out of the lane to the dock on the east wall (dock ~y +0.40)
    "D": (GoalPoint(-1.000, 0.800), GoalPoint(-0.750, -0.150)),
    "E": (GoalPoint(-0.400, -1.400), GoalPoint(-0.500, -2.950)),  # inside, a bit past the door
    "F": (GoalPoint(-1.400, -5.400), GoalPoint(-1.350, -4.700)),
}
GOALS = {room: slots[0] for room, slots in SLOTS.items()}  # slot 1

# B and E open onto the middle corridor through narrow side doors. Going into
# (or out of) them, the robot first stops at the crossroad centre in front of
# both doors (0.6 m from every wall), squares up, then drives straight in/out.
CROSSROAD = GoalPoint(-1.500, -2.180)
VIA_ROOMS = {"B", "E"}


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


def get_goal(room: str, slot: int = 1) -> GoalPoint:
    key = str(room).strip().upper()
    if key not in SLOTS:
        raise ValueError(f"알 수 없는 방: {room!r} (A~F)")
    if slot not in (1, 2):
        raise ValueError(f"자리는 1 또는 2: {slot!r}")
    return SLOTS[key][slot - 1]


def format_goals() -> str:
    return "\n".join(f"  {k}1 ({a.x:+.3f}, {a.y:+.3f})   {k}2 ({b.x:+.3f}, {b.y:+.3f})"
                      for k, (a, b) in SLOTS.items())
