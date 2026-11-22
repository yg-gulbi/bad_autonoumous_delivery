"""The traffic judgement FleetCore talks to: the joint plan (Stage 4O, D-43) and the common safety nets around it.

Up to the 4O 정리 this file chose between LEGACY (ResourceManager + ZoneTraffic) and PLAN; PLAN is the only one
now (tag 4o-dual-mode keeps the two-mode code)."""

from .collision import CollisionControl
from .config import FleetConfig
from .decisions import Decisions
from .legs import TickResult
from .plan_traffic import PlanManager
from .robot_protocol import DONE


class Traffic:
    """The two robots are planned together; the plan's order is kept while they drive (D-43)."""

    def __init__(self, config: FleetConfig, links: dict, *, plan_thread=False):
        self.config = config
        self.links = links
        self.resources = PlanManager(config, threaded=plan_thread)
        self.resources.links = links
        self.decisions = Decisions(self.resources.record)
        self.collision = CollisionControl(self.resources, links, self.decisions)

    def observe(self, now: float, reports: dict, mission_robots):
        for robot, link in self.links.items():
            self.resources.slot_support[robot] = bool(link.slots)
        for robot, unit in mission_robots.items():
            self.resources.robot_has_job[robot] = unit.job is not None
            self.resources.confirming[robot] = unit.job is not None and unit.state == "WAIT_CONFIRM"
        self.resources.observe(now, reports)

    def reconcile(self, now: float):
        self.collision.reconcile(now)

    def arbitrate(self, now: float) -> TickResult:
        return self.resources.arbitrate(now)

    def before_wire_move(self, robot: str, now: float):
        link = self.links[robot]
        move = link.move
        if (robot in self.resources.active and move and not move.finished and not move.remote_id
                and link.state in DONE):
            self.collision.access.before_departure(self.resources.active[robot], now)

    def traffic_stop(self, robot: str) -> bool:
        # Proximity stops and an operator's "that one first" hold a robot, except for a move apart from robots it
        # already stands too close to (clearance.leaving, 4O 후속 6); the plan lets out only such a position.
        apart = self.collision.may_move_apart(robot)
        held_near = robot in self.collision.contacts and not apart
        yielding = self.collision.yielding_priority(robot) and not apart
        return (robot in self.collision.access.waits or held_near or yielding or robot in self.resources.held)

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

    def unblock_robot(self, robot: str):
        self.resources.unblock_robot(robot)

    def arrived_aside(self, robot: str, data: dict, now: float) -> bool:
        return self.resources.arrived_aside(robot, data, now)

    def traffic_snapshot(self) -> dict:
        return self.resources.traffic_snapshot()

    def close(self):
        self.resources.runner.close()
