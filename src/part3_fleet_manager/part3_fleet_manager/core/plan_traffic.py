"""Stage 4O PLAN traffic: plan both robots together and keep the plan's order (ADG) while they drive (D-43).

``PlanManager`` is the traffic judgement (PLAN is the only one since the 4O 정리). Missions still request one leg
at a time. The planner (``planner.py``) decides, for every robot including idle ones, where each one goes and who
passes each conflict first. The parts, one file each:

- ``robot_state``  what the link and PART 04 say about each robot (connected, driving, paused, ...)
- ``plan_replan``  when to plan again, the planner's agents, the search, and its result as plans and dependencies
- ``plan_exec``    ADG execution: the conflict kinds and when their order counts as kept, stop lines, MOVE/RESUME
- ``plan_board``   the segment board as the plan uses it, UNKNOWN segments as fences, settling after a link loss
- ``plan_view``    labels and the snapshot parts of /fleet/state
- ``plan_runner``  the search outside the tick (a planning thread); its result is used in the next tick

PART 04 only receives MOVE <zone><slot>, PAUSE, RESUME and CANCEL. When the plan stops a robot at a slot that is
not its goal (moving aside), the MOVE goes to that slot; it is not a mission stop (no confirmation, no stop
record).
"""

from dataclasses import dataclass, field

from .legs import Leg, TickResult
from .plan_board import PlanBoard
from .plan_exec import PlanExec, PlanLeg, RobotPlan, _follows  # noqa: F401  (PlanLeg, RobotPlan, _follows: tests)
from .plan_graph import PlanGraph
from .plan_replan import PlanReplan
from .plan_runner import PlanRunner
from .plan_view import PlanView
from .planner import Planner
from .robot_state import RobotStates
from .segments import SegmentBoard

_GRAPHS: dict = {}


def plan_graph(config) -> PlanGraph:
    """A copy of the graph for this map geometry. The map's routes are built once per process; each copy adds
    and forgets the routes made for robot positions on its own."""
    key = repr((config.traffic.segments, config.traffic.links, config.traffic.graph, config.corridor, config.zones,
                config.slots, config.mock.speed_mps, config.safety.proximity_stop_m, config.safety.slot_stop_m,
                config.resources.zone_occupied_m, [(r, c.dock) for r, c in config.robots.items()]))
    graph = _GRAPHS.get(key)
    if graph is None or len(graph.routes) > 50000:
        graph = _GRAPHS[key] = PlanGraph(config)
    return graph.fork()


@dataclass
class PlanTick(TickResult):
    retarget: list = field(default_factory=list)    # robots whose current MOVE must stop for a new target
    arrived: list = field(default_factory=list)     # (robot, zone, slot): already standing in the goal slot


class PlanManager(PlanView, PlanBoard, PlanReplan, PlanExec):
    def __init__(self, config, *, threaded=False):
        self.config = config
        self.gcfg = config.traffic.graph
        self.robot_order = {robot: i for i, robot in enumerate(config.robots)}
        self.events: list[dict] = []
        self.board = SegmentBoard(config, self.record, self.robot_label)
        self.map = self.board.map
        self.segments = self.board.segments
        self.graph = plan_graph(config)
        self.planner = Planner(self.graph)
        self.robots = RobotStates(config, self.graph, self.map)
        self.pending: dict[str, Leg] = {}
        self.active: dict[str, Leg] = {}
        self.reports: dict = {}
        self.regions: dict[str, str] = {}
        self._origins: dict[str, str] = {}
        self.slot_choice: dict = {}
        self.wait_reasons: dict[str, str] = {}
        self.slot_support = dict.fromkeys(config.robots, True)
        self.robot_has_job = dict.fromkeys(config.robots, False)
        self.confirming = dict.fromkeys(config.robots, False)   # WAIT_CONFIRM: kept still by the plan (D-56)
        self.levels: dict = {}                            # leg -> Level after an operator change (SET_LEVEL)
        # plan
        self.plans: dict[str, RobotPlan] = {}
        self.plan_info: dict = {}
        self.dirty: str | None = "시작"
        self._marks = 0
        self._last_replan = float("-inf")
        self._late_replan = float("-inf")
        self.coast_route: dict[str, tuple] = {}          # robot that may still drive its MOVE -> remaining pieces
        self.fenced: dict[str, tuple] = {}               # robot whose every way crosses an UNKNOWN segment (D-54)
        self.runner = PlanRunner(self, threaded=threaded)
        # execution
        self.held: dict[str, dict] = {}
        self.go: set[str] = set()                        # robots the plan lets start (MOVE) or go on (RESUME) now
        self.aside_home: dict[str, str | tuple] = {}      # idle robot moved aside -> spot to come back to
        self.aside_moves: dict[str, str] = {}             # idle robot -> endpoint of the MOVE we sent
        self._clear_since: dict = {}
        self._retarget_sent: dict[str, object] = {}
        self._aside_seq = 0

    @property
    def links(self):
        return self.robots.links

    @links.setter
    def links(self, links):
        self.robots.links = links

    @property
    def parked_at(self):
        return self.robots.parked_at

    @property
    def departed_from(self):
        return self.robots.departed_from

    def record(self, event_type, now, reason, *, robot=None, mission_id=None, candidates=None, **detail):
        self.events.append({"v": 1, "ts": now, "type": event_type, "robot": robot, "mission_id": mission_id,
                            "detail": {"reason": reason, "candidates": candidates or [], **detail}})
        for name in detail.get("resources", ()):
            if name in self.segments and event_type in ("RESOURCE_RELEASE", "RESOURCE_UNKNOWN",
                                                         "RESOURCE_CLEAR_CONFIRM", "RESOURCE_ENTER"):
                self.segments[name].reason, self.segments[name].decided_at = reason, now

    # --- legs (the mission layer asks for one leg at a time) -----------------------------------------------

    def request(self, robot, destination, level, now, *, mission_id=None, carrying_patient=False,
                mission_type=None, dock_path=()) -> Leg:
        if robot not in self.config.robots or destination not in self.config.zones:
            raise ValueError("unknown robot or destination")
        if type(level) is not int or not 1 <= level <= 3:
            raise ValueError("level must be an integer from 1 to 3")
        if robot in self.pending or robot in self.active:
            raise ValueError(f"{robot} already has a leg")
        leg = Leg(robot, destination, level, now, mission_id, carrying_patient, mission_type, dock_path)
        self.pending[robot] = leg
        self.aside_home.pop(robot, None)
        self.record("RESOURCE_REQUEST", now, f"{self.robot_label(robot)} — {self.zone_label(destination)}으로 가는 구간 요청",
                    robot=robot, mission_id=mission_id, destination=destination, level=level)
        self._mark("새 구간 요청")
        return leg

    def finish(self, leg, now) -> bool:
        robot = leg.robot
        if self.pending.get(robot) is leg:
            del self.pending[robot]
        elif self.active.get(robot) is leg:
            del self.active[robot]
        else:
            return False
        self.slot_choice.pop(leg, None)
        self.levels.pop(leg, None)
        self._origins.pop(robot, None)
        self.wait_reasons.pop(robot, None)
        self._mark("구간 종료")
        return True

    def slot_of(self, leg) -> int:
        return self.slot_choice.get(leg, 1)

    def move_target(self, leg):
        """(zone, slot) for the MOVE of this leg: its goal slot, the aside slot the plan stops at first, or
        the goal slot the robot already stands in (that MOVE only reports the arrival). A dock leg goes to
        its aside slot first too, else ("DOCK", None)."""
        plan = self.plans.get(leg.robot)
        if plan and (plan.legs or plan.entries):
            name = plan.legs[0].target if plan.legs else plan.entries[0].point
            point = self.graph.points.get(name)
            if point is not None and point.kind == "slot" and point.zone == (
                    leg.destination if not plan.legs else point.zone):
                if not leg.dock_path or (plan.legs and plan.legs[0].aside):
                    return point.zone, point.slot
        if leg.dock_path:
            return "DOCK", None
        return leg.destination, self.slot_of(leg)

    def arrived_aside(self, robot, data, now) -> bool:
        """An ARRIVED at the aside slot of a mission leg: the leg waits again and the plan is made again.
        Judged by the MOVE that was sent (a slot that is not a goal of the leg), not by the current plan: the
        arrival may come back only with a STATE after a link loss, when the robot had no plan."""
        leg = self.active.get(robot)
        view = self.robots.view(robot)
        if leg is None or view.move_zone in (None, "DOCK") or data.get("destination") != view.move_zone:
            return False
        name = self.map.slot_names.get((view.move_zone, view.move_slot or 1))
        if name is None or name in self._goal_endpoints(robot, leg):
            return False
        point = self.graph.points[name]
        del self.active[robot]
        self.pending[robot] = leg
        self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} · {self.endpoint_label(point.name)} 도착, 길 비켜 주는 중",
                    robot=robot, mission_id=leg.mission_id, target=point.name, arrived=True)
        self._mark("비켜선 자리 도착")
        return True

    def level_of(self, leg):
        return self.levels.get(leg, leg.level)

    def set_level(self, leg, level):
        """The mission's Level changed: the same leg is planned again with the new Level's weight."""
        self.levels[leg] = level
        self._mark("응급도 변경")

    def robot_state(self, robot, base_state):
        report = self.reports.get(robot)
        if base_state == "SAFE_PAUSE" or (report and report.safe_pause):
            return "SAFE_PAUSE"
        if base_state in {"OFFLINE", "FAULT"}:
            return base_state
        if robot in self.held and base_state in ("MOVING", "SENDING"):
            return "HOLD"
        return base_state

    # --- one tick ------------------------------------------------------------------------------------------

    def observe(self, now, reports):
        self.reports = dict(reports)
        self.regions = {r: self.config.corridor.region_of(s.pose) for r, s in reports.items()
                        if s.link_ok and s.pose is not None}
        changes, back = self.robots.observe(now, reports)
        for reason in changes:
            self._mark(reason)
        self._idle_arrivals(now)
        self.robots.settle_parking()
        for robot, plan in self.plans.items():
            self._progress(robot, plan)
        # Before the board counts the robot inside where it is now: which of those segments were not its own.
        foreign = {robot: [n for n in self.board.at(robot, self.reports[robot].pose, self._slot_member)
                           if robot not in self.segments[n].holders] for robot in back}
        self.board.observe(now, reports, self._slot_member)
        for robot in back:
            self._link_back(robot, now, foreign[robot])

    def arbitrate(self, now) -> PlanTick:
        """Plan again when due (plan_runner: the result comes in the next tick), then run the plan."""
        ready = self.runner.tick(now)
        return self.execute(now, PlanTick([], []), starts=ready)
