"""What one tick hands between the mission layer, the plan and the wire (moved out of the LEGACY resource
manager when PLAN became the only traffic judgement, 4O 정리)."""

from dataclasses import dataclass

from . import geometry as g


@dataclass(frozen=True)
class RobotReport:
    pose: g.Point | None
    link_ok: bool
    safe_pause: bool = False
    can_yield: bool = True
    stalled: bool = False
    nav_paused: bool = False
    departed: bool = False
    via: bool | None = None      # this move goes through the crossroad (from NAVIGATING)
    via_passed: bool = False     # VIA_PASSED received for this move
    proximity_stopped: bool = False

    @property
    def stopped(self):
        return self.safe_pause or self.stalled or self.nav_paused or not self.can_yield or not self.link_ok


@dataclass(frozen=True, eq=False)
class Leg:
    robot: str
    destination: str
    level: int
    since: float
    mission_id: str | None
    carrying_patient: bool
    mission_type: str | None = None
    dock_path: tuple[g.Point, ...] = ()  # Dock approach and contact points in map coordinates.


@dataclass(frozen=True)
class MotionRequest:
    robot: str
    command: str


@dataclass
class TickResult:
    grants: list[Leg]
    commands: list[MotionRequest]
