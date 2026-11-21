"""Traffic engine selector: LEGACY (existing ResourceManager + ZoneTraffic) vs PLAN (Stage 4O)."""

from . import geometry as g
from .collision import CollisionControl
from .config import FleetConfig
from .decisions import Decisions
from .resource_manager import ResourceManager, TickResult
from .robot_protocol import DONE
from .plan_traffic import PlanManager


class LegacyTraffic:
    """Preserves the exact pre-4O traffic arbitration, GiveWay, doors, ZoneTraffic, and E-423 rules."""

    mode = "LEGACY"

    def __init__(self, config: FleetConfig, links: dict):
        self.config = config
        self.links = links
        self.resources = ResourceManager(config)
        self.decisions = Decisions(self.resources.record)
        self.collision = CollisionControl(self.resources, links, self.decisions, enable_zone_traffic=True)

    def observe(self, now: float, reports: dict, mission_robots=None):
        del mission_robots
        self.resources.observe(now, reports)

    def reconcile(self, now: float):
        self.collision.reconcile_zones(now)

    def arbitrate(self, now: float) -> TickResult:
        return self.resources.arbitrate(now, self.collision.zone_held)

    def before_wire_move(self, robot: str, now: float):
        link = self.links[robot]
        move = link.move
        access = self.collision.access.waits.get(robot)
        if (robot in self.resources.active and move and not move.finished and not move.remote_id
                and link.state in DONE and not (access and access.rule == "OUTBOUND")):
            self.collision.access.before_departure(self.resources.active[robot], now)

    def traffic_stop(self, robot: str) -> bool:
        return (robot in self.collision.access.waits or robot in self.collision.contacts
                or robot in self.collision.zone or self.collision.yielding_priority(robot))

    def plan_hold(self, robot: str) -> bool:
        del robot
        return False

    def may_start(self, robot: str) -> bool:
        del robot
        return True

    def parked_zone(self, robot: str):
        del robot
        return None

    def arrived_aside(self, robot: str, data: dict, now: float) -> bool:
        del robot, data, now
        return False

    def block_robot(self, robot: str, pose, now: float):
        region = self.config.corridor.region_of(pose) if pose is not None else None
        resource = self.resources.resources.get(region)
        if resource is not None:
            if not resource.stall_blockers:
                resource.blocked_since = now
            resource.stall_blockers.add(robot)
        return region

    def unblock_robot(self, robot: str, region: str | None):
        if region in self.resources.resources:
            res = self.resources.resources[region]
            res.stall_blockers.discard(robot)
            if not res.stall_blockers:
                res.blocked_since = None

    def cancel_policy(self, robot: str, state: str, link, now: float) -> str:
        corridor = self.resources.resources[g.CORRIDOR]
        holding = corridor.holders.get(robot)
        pose = link.pose
        inside = pose is not None and self.config.corridor.region_of(pose) == g.CORRIDOR
        uncertain = holding and (corridor.uncertain or not link.online(now))
        within_margin = holding and holding.state == "OCCUPIED" and (
            pose is None or not self.resources.planner.is_clear(
                g.CORRIDOR, pose, self.config.resources.clear_margin_m))
        if state != "FAULT" and (inside or uncertain or within_margin):
            raise ValueError("E-423: 복도 통과 후 취소하세요.")
        return "IMMEDIATE"

    def level_policy(self):
        raise ValueError("E-409: LEGACY 교통 판단에서는 응급도를 바꿀 수 없습니다.")

    def traffic_snapshot(self) -> dict:
        return {
            "mode": "LEGACY",
            "stop_margin_m": self.config.traffic.stop_margin_m,
            "segments": {},
            "stop_line_waits": {},
        }


class PlanTraffic:
    """Stage 4O: the two robots are planned together; the plan's order is kept while they drive (D-43)."""

    mode = "PLAN"

    def __init__(self, config: FleetConfig, links: dict):
        self.config = config
        self.links = links
        self.resources = PlanManager(config)
        self.resources.links = links
        self.decisions = Decisions(self.resources.record)
        self.collision = CollisionControl(self.resources, links, self.decisions, enable_zone_traffic=False)

    def observe(self, now: float, reports: dict, mission_robots=None):
        for robot, link in self.links.items():
            self.resources.slot_support[robot] = bool(link.slots)
        if mission_robots is not None:
            for robot, unit in mission_robots.items():
                self.resources.robot_has_job[robot] = unit.job is not None
                self.resources.confirming[robot] = unit.job is not None and unit.state == "WAIT_CONFIRM"
        self.resources.observe(now, reports)

    def reconcile(self, now: float):
        self.collision.reconcile_zones(now)

    def arbitrate(self, now: float) -> TickResult:
        return self.resources.arbitrate(now)

    def before_wire_move(self, robot: str, now: float):
        link = self.links[robot]
        move = link.move
        if (robot in self.resources.active and move and not move.finished and not move.remote_id
                and link.state in DONE):
            self.collision.access.before_departure(self.resources.active[robot], now)

    def traffic_stop(self, robot: str) -> bool:
        return (robot in self.collision.access.waits or robot in self.collision.contacts
                or self.collision.yielding_priority(robot) or robot in self.resources.held)

    def plan_hold(self, robot: str) -> bool:
        return robot in self.resources.held

    def may_start(self, robot: str) -> bool:
        """Only the plan's executor lets a MOVE or RESUME out to PART 04 (D-55): an operator resume, a card
        choice or a retry only lifts its own stop; the robot stays still while the plan says wait."""
        return robot in self.resources.go

    def parked_zone(self, robot: str):
        """Zone of the slot the robot stands in after a completed MOVE ("" when stopped on the way)."""
        slot = self.resources.parked_at.get(robot)
        return self.resources.graph.points[slot].zone if slot else ""

    def block_robot(self, robot: str, pose, now: float):
        region = self.config.corridor.region_of(pose) if pose is not None else None
        self.resources.block_robot(robot, pose, now)
        return region

    def unblock_robot(self, robot: str, region: str | None):
        del region
        self.resources.unblock_robot(robot)

    def cancel_policy(self, robot: str, state: str, link, now: float) -> str:
        del robot, state, link, now
        return "IMMEDIATE"      # a cancel is a new goal; the plan takes the robot out (D-46)

    def level_policy(self):
        """A Level change only changes the plan's weight; the plan is made again (D-48)."""

    def arrived_aside(self, robot: str, data: dict, now: float) -> bool:
        return self.resources.arrived_aside(robot, data, now)

    def traffic_snapshot(self) -> dict:
        return self.resources.traffic_snapshot()


def create_traffic(config: FleetConfig, links: dict):
    if config.traffic.mode == "PLAN":
        return PlanTraffic(config, links)
    return LegacyTraffic(config, links)
