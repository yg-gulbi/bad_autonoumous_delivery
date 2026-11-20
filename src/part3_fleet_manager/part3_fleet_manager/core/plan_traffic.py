"""Stage 4O PLAN traffic: plan both robots together and keep the plan's order (ADG) while they drive.

``PlanManager`` takes the place of the resource manager in PLAN mode. Missions still request one leg at a
time. The planner (``planner.py``) decides, for every robot including idle ones, where each one goes and
who passes each conflict first. The plan is then turned into an action dependency graph (Hönig et al.
2019): every robot has a list of positions, and for every conflicting pair of positions the robot planned
second may enter only after the robot planned first has left (``clear_margin_m`` for ``clear_hold_s``).
Times are not kept, only the order, so a robot that is late is still safe.

A robot whose next position is not cleared is held before the stop line (``stop_margin_m`` before that
position): a MOVE is not sent yet, or the moving robot gets PAUSE and later RESUME. PART 04 only receives
MOVE <zone><slot>, PAUSE, RESUME and CANCEL. When the plan stops a robot at a slot that is not its goal
(moving aside), the MOVE goes to that slot; it is not a mission stop (no confirmation, no stop record).

The plan is made again on a new leg request, a finished or cancelled leg, arrival at an aside slot, a
link recovery or loss, a safety stop release, and when a robot is ``replan_delay_s`` behind the plan.
"""

from dataclasses import dataclass, field
from math import dist, hypot
import time

from . import geometry as g
from .plan_graph import PlanGraph, project
from .planner import SLOT2_PENALTY, Agent, Planner
from .resource_manager import Leg, MotionRequest, RobotReport, TickResult
from .robot_protocol import DONE
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


@dataclass
class Entry:
    piece: int
    point: str
    leg: int            # index into RobotPlan.legs; -1 = standing before the first leg
    s0: float           # arc length on that leg's line where the position starts
    s1: float
    t0: int             # plan steps
    t1: int
    standing: bool


@dataclass
class PlanLeg:
    route: int
    target: str         # endpoint the MOVE goes to
    aside: bool         # not a goal of the robot (moving out of the way)
    line: tuple
    length: float


@dataclass
class RobotPlan:
    legs: list
    entries: list
    deps: dict = field(default_factory=dict)        # entry index -> [(robot, entry index)]
    s: float = 0.0
    entry: int = 0
    started: bool = False                           # MOVE for legs[0] sent
    failed: bool = False                            # no route found: keep still until the next plan


class _WaitView:
    """Exposes a plan wait to the deadlock wait-for graph (last safety net, shared with LEGACY)."""

    def __init__(self, peers):
        self.peers = tuple(peers)
        self.uncertain = set()
        self.holders = dict.fromkeys(self.peers)
        self.state = "OCCUPIED"

    def blockers(self, robot):
        return tuple(p for p in self.peers if p != robot)


class PlanManager:
    def __init__(self, config):
        self.config = config
        self.gcfg = config.traffic.graph
        self.robot_order = {robot: i for i, robot in enumerate(config.robots)}
        self.events: list[dict] = []
        self.last_decision = None
        self.board = SegmentBoard(config, self.record, self.robot_label)
        self.map = self.board.map
        self.segments = self.board.segments
        self.resources = dict(self.segments)
        self.graph = plan_graph(config)
        self.planner = Planner(self.graph)
        self.links: dict = {}
        self.pending: dict[str, Leg] = {}
        self.active: dict[str, Leg] = {}
        self.reports: dict[str, RobotReport] = {}
        self.regions: dict[str, str] = {}
        self._origins: dict[str, str] = {}
        self.slot_choice: dict = {}
        self.wait_reasons: dict[str, str] = {}
        self.slot_support = dict.fromkeys(config.robots, True)
        self.robot_has_job = dict.fromkeys(config.robots, False)
        self.plans: dict[str, RobotPlan] = {}
        self.plan_info: dict = {}
        self.dirty: str | None = "시작"
        self.held: dict[str, dict] = {}
        self.aside_home: dict[str, str | tuple] = {}      # idle robot moved aside -> spot to come back to
        self.aside_moves: dict[str, str] = {}             # idle robot -> endpoint of the MOVE we sent
        self._clear_since: dict = {}
        self._last_replan = float("-inf")
        self._late_replan = float("-inf")
        self._link_ok: dict[str, bool] = {}
        self._safe: dict[str, bool] = {}
        self._retarget_sent: dict[str, object] = {}
        self._aside_seq = 0
        self._finishing = None
        self.parked_at: dict[str, str | None] = {}       # slot the robot stands in after its last MOVE
        self.departed_from: dict[str, str | None] = {}   # slot the robot's current MOVE started from
        self._last_target: dict[str, str | None] = {}
        self.yield_events: list = []

    # --- labels and events ----------------------------------------------------------------------

    def robot_label(self, robot):
        return self.config.robots[robot].label

    def zone_label(self, zone, slot=None):
        name = self.config.zone_names.get(zone) or f"{zone} 구역"
        return f"{name} 2번 자리" if slot == 2 else name

    def resource_label(self, name):
        if name == g.CORRIDOR:
            return "복도"
        if name in self.segments:
            return self.segments[name].spec.label
        if name.startswith("PLAN_WAIT:"):
            return "계획 순서"
        if name.startswith("ROOM_"):
            return self.zone_label(name.removeprefix("ROOM_")) + " 출입"
        return name

    def endpoint_label(self, name):
        point = self.graph.points[name]
        if point.kind == "slot":
            return self.zone_label(point.zone, point.slot)
        return point.label

    def record(self, event_type, now, reason, *, robot=None, mission_id=None, candidates=None, **detail):
        self.events.append({"v": 1, "ts": now, "type": event_type, "robot": robot, "mission_id": mission_id,
                            "detail": {"reason": reason, "candidates": candidates or [], **detail}})
        for name in detail.get("resources", ()):
            if name in self.segments and event_type in ("RESOURCE_RELEASE", "RESOURCE_UNKNOWN",
                                                         "RESOURCE_CLEAR_CONFIRM", "RESOURCE_ENTER"):
                self.segments[name].reason, self.segments[name].decided_at = reason, now

    # --- legs (same interface as the resource manager) ------------------------------------------

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

    def begin_tick(self):
        self._finishing = []

    def finish(self, leg, now) -> bool:
        robot = leg.robot
        if self.pending.get(robot) is leg:
            del self.pending[robot]
        elif self.active.get(robot) is leg:
            del self.active[robot]
        else:
            return False
        self.slot_choice.pop(leg, None)
        self._origins.pop(robot, None)
        self.wait_reasons.pop(robot, None)
        self._mark("구간 종료")
        return True

    def slot_of(self, leg) -> int:
        return self.slot_choice.get(leg, 1)

    def move_target(self, leg):
        """(zone, slot) for the MOVE of this leg: its goal slot, the aside slot the plan stops at first, or
        the goal slot the robot already stands in (that MOVE only reports the arrival)."""
        plan = self.plans.get(leg.robot)
        if plan and (plan.legs or plan.entries):
            name = plan.legs[0].target if plan.legs else plan.entries[0].point
            point = self.graph.points.get(name)
            if point is not None and point.kind == "slot" and point.zone == (
                    leg.destination if not plan.legs else point.zone):
                return point.zone, point.slot
        return leg.destination, self.slot_of(leg)

    def arrived_aside(self, robot, data, now) -> bool:
        """An ARRIVED at the aside slot of a mission leg: the leg waits again and the plan is made again."""
        leg = self.active.get(robot)
        plan = self.plans.get(robot)
        if leg is None or not plan or not plan.legs or not plan.legs[0].aside:
            return False
        point = self.graph.points[plan.legs[0].target]
        if data.get("destination") != point.zone:
            return False
        del self.active[robot]
        self.pending[robot] = leg
        self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} · {self.endpoint_label(point.name)} 도착, 길 비켜 주는 중",
                    robot=robot, mission_id=leg.mission_id, target=point.name, arrived=True)
        self._mark("비켜선 자리 도착")
        return True

    def eligible(self, robot) -> bool:
        report = self.reports.get(robot)
        return bool(report and report.link_ok and report.pose is not None and not report.safe_pause)

    def needed(self, robot):
        name = f"PLAN_WAIT:{robot}"
        return (name,) if robot in self.pending and name in self.resources else ()

    def effective_level(self, leg, now):
        del now
        return leg.level

    def following(self, robot) -> bool:
        del robot
        return False

    def is_stop_line_paused(self, robot) -> bool:
        return robot in self.held

    def robot_state(self, robot, base_state):
        report = self.reports.get(robot)
        if base_state == "SAFE_PAUSE" or (report and report.safe_pause):
            return "SAFE_PAUSE"
        if base_state in {"OFFLINE", "FAULT"}:
            return base_state
        if robot in self.held and base_state in ("MOVING", "SENDING"):
            return "HOLD"
        return base_state

    def clear_unknown(self, name, by, now):
        self.board.clear_unknown(name, by, now)
        self._mark("점유 확인")

    def block_robot(self, robot, pose, now):
        return self.board.block(robot, pose, now)

    def unblock_robot(self, robot):
        self.board.unblock(robot)

    def _mark(self, reason):
        if self.dirty is None:
            self.dirty = reason

    # --- observation ----------------------------------------------------------------------------

    def observe(self, now, reports):
        self.reports = dict(reports)
        self.regions = {r: self.config.corridor.region_of(s.pose) for r, s in reports.items()
                        if s.link_ok and s.pose is not None}
        for robot, report in reports.items():
            ok = bool(report.link_ok and report.pose is not None)
            if ok and self._link_ok.get(robot, ok) != ok:
                self._mark("연결 복구")
            self._link_ok[robot] = ok
            if self._safe.get(robot) and not report.safe_pause:
                self._mark("안전 정지 해제")
            self._safe[robot] = report.safe_pause
        self._idle_arrivals(now)
        self._parking(reports)
        for robot, plan in self.plans.items():
            self._progress(robot, plan)
        self.board.observe(now, reports, self._slot_member)

    def _parking(self, reports):
        """A robot stands in a slot when its MOVE to that slot completed (or it started there). A robot
        stopped on the way (cancel, failure) stands at a free position, even inside another slot's circle."""
        for robot, report in reports.items():
            link = self.links.get(robot)
            if link is None or report.pose is None:
                continue
            if self._moving(robot):
                self._last_target[robot] = self._move_endpoint(robot)
                if self.parked_at.get(robot) is not None:
                    self.departed_from[robot] = self.parked_at[robot]
                self.parked_at[robot] = None
                continue
            self.departed_from.pop(robot, None)
            if robot not in self.parked_at:
                self.parked_at[robot] = self.map.nearest_slot(report.pose)
            unsent = link.move is not None and not link.move.finished and not link.move.attempts
            if robot in self._last_target and link.state in DONE and not link.cancel_wait and not unsent:
                target = self._last_target.pop(robot)
                point = self.graph.points.get(target) if target else None
                arrived = link.state == "COMPLETE" and point is not None and point.region.contains(report.pose)
                self.parked_at[robot] = target if arrived and point.kind == "slot" else None

    def _slot_member(self, robot, slot):
        """A slot counts a robot standing there, or driving a position that starts or ends there."""
        if not self._moving(robot):
            return self.parked_at.get(robot) == slot
        plan = self.plans.get(robot)
        if plan is None or not plan.entries:
            return True
        return slot in self.graph.segs(plan.entries[min(plan.entry, len(plan.entries) - 1)].piece)

    def _idle_arrivals(self, now):
        """End of a MOVE we sent to a robot without a mission (aside, or back to its spot)."""
        for robot, target in list(self.aside_moves.items()):
            link = self.links.get(robot)
            if link is None or link.cancel_wait or (link.move is not None and not link.move.finished):
                continue
            arrived = link.state == "COMPLETE" and link.pose is not None and \
                self.graph.points[target].region.contains(link.pose)
            if link.move is not None and link.move.leg_id.startswith("Y-"):
                link.forget()
            del self.aside_moves[robot]
            self._last_target.pop(robot, None)
            self.parked_at[robot] = target if arrived else None
            if arrived and self.aside_home.get(robot) == target:
                self.aside_home.pop(robot, None)
                self._mark("자리로 돌아옴")
            elif arrived:
                self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} · {self.endpoint_label(target)} 도착, 길 비켜 주는 중",
                            robot=robot, target=target, arrived=True)
                self._mark("비켜선 자리 도착")
            else:
                self._mark("비켜서기 이동 멈춤")

    def _progress(self, robot, plan):
        report = self.reports.get(robot)
        if report is None or report.pose is None or not plan.legs:
            return
        if not self._on_first_leg(robot, plan):
            return
        leg = plan.legs[0]
        s = project(leg.line, report.pose, max(0.0, plan.s - 0.05), plan.s + 1.0)
        plan.s = max(plan.s, s)
        current = plan.entry
        for i, entry in enumerate(plan.entries):
            if entry.leg == 0 and not entry.standing and entry.s0 <= plan.s + 1e-6:
                current = max(current, i)
        plan.entry = current

    def _move_endpoint(self, robot):
        link = self.links.get(robot)
        move = link.move if link else None
        if move is None or move.finished:
            return None
        if move.zone == "DOCK":
            return next((n for n, p in self.graph.endpoints.items() if p.kind == "dock"
                         and self.config.robots[robot].dock and p.anchor == (self.config.robots[robot].dock.x,
                                                                              self.config.robots[robot].dock.y)), None)
        return self.map.slot_names.get((move.zone, move.slot or 1))

    def _moving(self, robot):
        """True while a MOVE was sent and the robot has not finished it (it may still start or coast)."""
        link = self.links.get(robot)
        if link is None or link.move is None or link.move.finished or link.cancel_wait:
            return False
        return bool(link.move.attempts) and (link.state not in DONE or not link.move.remote_id)

    def _coasting(self, robot):
        """A robot we cannot steer right now (CANCEL not confirmed yet, or link lost) that has a MOVE it may
        still be driving."""
        link = self.links.get(robot)
        if link is None or link.move is None or link.move.finished or not link.move.attempts:
            return False
        report = self.reports.get(robot)
        return bool(link.cancel_wait or report is None or not report.link_ok)

    def _on_first_leg(self, robot, plan):
        return plan.started and self._moving(robot) and self._move_endpoint(robot) == plan.legs[0].target

    # --- planning -------------------------------------------------------------------------------

    def _still_held(self, robot):
        """Capacity segments the board still gives to this robot (not yet released after it left)."""
        return tuple((name, seg.holders[robot].direction) for name, seg in self.segments.items()
                     if name in self.graph.capacity and robot in seg.holders
                     and (seg.holders[robot].state == "OCCUPIED" or robot in seg.uncertain))

    def _goal_endpoints(self, robot, leg):
        if leg.dock_path:
            dock = self.config.robots[robot].dock
            return [n for n, p in self.graph.endpoints.items() if p.kind == "dock" and dock and p.anchor == (dock.x, dock.y)]
        count = self.config.slot_count(leg.destination) if self.slot_support.get(robot, True) else 1
        return [self.map.slot_names[(leg.destination, s)] for s in range(1, count + 1)]

    def _aside_targets(self, robot, start_xy, exclude_zone, here):
        if not self.gcfg.aside_candidates:
            return []
        slots = [n for n, p in self.graph.endpoints.items() if p.kind == "slot" and p.zone != exclude_zone
                 and n != here and (self.slot_support.get(robot, True) or p.slot == 1)]
        if here in self.graph.from_endpoint:
            table = self.graph.from_endpoint[here]
            steps = {n: self.graph.route_steps(table[n]) for n in slots if n in table}
        else:
            steps = {n: self.graph.route_steps(self.graph.route_from(start_xy, n)) for n in slots}
        return sorted(steps, key=lambda n: (steps[n], n))[:self.gcfg.aside_candidates]

    def _agent(self, robot):
        """Agent for the planner, or None when the robot cannot be placed on the map."""
        report = self.reports.get(robot)
        link = self.links.get(robot)
        pose = report.pose if report and report.pose is not None else (link.pose if link else None)
        if pose is None:
            return None
        leg = self.active.get(robot) or self.pending.get(robot)
        stop_m = self.config.traffic.stop_margin_m
        static = (report is None or not report.link_ok or report.pose is None or report.safe_pause
                  or report.stalled or (link is not None and link.cancel_wait))
        here = self.parked_at.get(robot)
        if here is not None and not self.graph.points[here].region.contains(pose):
            here = None
        if here is None and any(p.kind == "dock" and p.region.contains(pose) for p in self.graph.endpoints.values()):
            here = next(n for n, p in self.graph.endpoints.items() if p.kind == "dock" and p.region.contains(pose))
        moving = self._moving(robot) and not static
        target = self._move_endpoint(robot) if moving else None
        if leg is not None:
            goals = self._goal_endpoints(robot, leg)
            weight = self.gcfg.level_weights.get(leg.level, 1.0)
            goal_zone = leg.destination
        else:
            weight = self.gcfg.idle_weight
            home = self.aside_home.get(robot)
            goals = [home] if isinstance(home, str) else []
            goal_zone = self.graph.endpoints[home].zone if isinstance(home, str) else (
                self.graph.endpoints[here].zone if here in self.graph.endpoints else None)
        free_routes = {}
        starts = []
        if moving and target is not None:
            via_done = bool(link.move.via_passed or link.move.via is False)
            origin = self.departed_from.get(robot)
            if origin is not None and not self.graph.points[origin].region.contains(pose):
                origin = None
            extra = self._still_held(robot)
            # A robot driving cannot stop before its stop line (stop_margin_m ahead), so the points up to there
            # are fixed; the route is cut at the stop line, so it can still wait right there. A robot that has
            # already stopped (PAUSED) can stay where it is.
            driving = link.state in ("MOVING", "UNDOCKING")
            cut = stop_m if driving else None
            rid = self.graph.route_from(pose, target, start=origin, via_done=via_done, extra=extra, cut=cut)
            route = self.graph.routes[rid]
            until = max((j for j, s0 in enumerate(route.starts) if j > 0 and s0 < stop_m - 1e-6), default=None) \
                if driving else None
            starts.append((rid, until))
            first = route.line[1] if len(route.line) > 1 else route.line[0]
            heading = (first[0] - pose[0], first[1] - pose[1])
            norm = hypot(*heading) or 1.0
            committed = [self.graph.pieces[p].point for p in route.pieces[:(until or 0) + 1]]
            for other in [*goals, *self._aside_targets(robot, pose, goal_zone, None)]:
                if other == target:
                    continue
                alt = self.graph.route_from(pose, other, start=origin, via_done=via_done, extra=extra, cut=cut)
                alt_route = self.graph.routes[alt]
                if len(alt_route.line) < 2 or not alt_route.pieces:
                    continue
                step = (alt_route.line[1][0] - pose[0], alt_route.line[1][1] - pose[1])
                cos = (step[0] * heading[0] + step[1] * heading[1]) / (norm * (hypot(*step) or 1.0))
                alt_points = [self.graph.pieces[p].point for p in alt_route.pieces[:len(committed)]]
                if cos > 0.7 and alt_points == committed:
                    starts.append((alt, until))
            stand_goal = None
        else:
            extra = self._still_held(robot)
            if here is not None:
                stand_goal = self.graph.parked(here, extra)
                rid = self.graph.stay_route(here, piece=stand_goal)
            else:
                stand_goal = self.graph.stand(pose, extra)
                rid = self.graph.stay_route(None, piece=stand_goal)
            starts.append((rid, None))
        if leg is None and not goals:
            if stand_goal is not None and here is None:
                # Stopped on the way without a mission: staying is free, and so is parking in a nearby slot
                # (it cannot drive back to a free position once it has moved aside).
                near = self._aside_targets(robot, pose, None, None)[:2]
                goals_pieces = {stand_goal: 0.0, **{self.graph.park[n]: 0.0 for n in near}}
                goals = near
            elif stand_goal is not None:
                goals_pieces = {stand_goal: 0.0, self.graph.park[here]: 0.0}
                goals = [here] if here is not None else []
            elif target is not None:
                goals_pieces = {self.graph.park[target]: 0.0}
                goals = [target]
            else:
                goals_pieces = {}
        else:
            goals_pieces = {self.graph.park[n]: (SLOT2_PENALTY if self.graph.endpoints[n].slot == 2 else 0.0)
                            for n in goals}
            if here in goals and stand_goal is not None:
                goals_pieces[stand_goal] = goals_pieces[self.graph.park[here]]
        if not goals_pieces:
            static = True
        aside = self._aside_targets(robot, pose, goal_zone, here) if not static else []
        targets = tuple(dict.fromkeys([*aside, *goals]))
        if here is None and not moving and not static:
            free_routes[rid] = [self.graph.route_from(pose, n, extra=extra) for n in targets]
        blocks = ()
        coast_target = self._move_endpoint(robot) if static and self._coasting(robot) else None
        if coast_target is not None:
            # CANCEL not confirmed yet, or link lost while driving: the robot may still go on to its MOVE
            # target, so block that way.
            origin = self.departed_from.get(robot)
            if origin is not None and not self.graph.points[origin].region.contains(pose):
                origin = None
            via_done = bool(link.move.via_passed or link.move.via is False)
            rid = self.graph.route_from(pose, coast_target, start=origin, via_done=via_done,
                                        extra=self._still_held(robot))
            blocks = tuple(self.graph.routes[rid].pieces)
        agent = Agent(robot, weight, starts, goals_pieces, targets, tuple(goals), free_routes, static=static,
                      blocks=blocks)
        return agent

    def replan(self, now, reason):
        agents = [a for a in (self._agent(r) for r in self.config.robots) if a is not None]
        result = self.planner.plan(agents, limit_ms=self.gcfg.plan_limit_ms)
        self._last_replan = now
        self.dirty = None
        old = self.plans
        self.plans = {}
        goal_names = {a.name: set(a.goal_targets) for a in agents}
        for agent in agents:
            events = result.events.get(agent.name)
            if not events or agent.static:
                continue
            self.plans[agent.name] = self._robot_plan(agent.name, events, goal_names[agent.name], old.get(agent.name))
            if agent.name in result.failed:
                self.plans[agent.name].failed = True
                self.plans[agent.name].legs = []
        self._dependencies()
        keep_routes = {leg.route for plan in self.plans.values() for leg in plan.legs}
        keep_pieces = {entry.piece for plan in self.plans.values() for entry in plan.entries}
        if self.graph.prune(keep_routes, keep_pieces):
            self.planner.forget()
        for robot, plan in self.plans.items():
            leg = self.active.get(robot) or self.pending.get(robot)
            goal = plan.legs[-1].target if plan.legs else (plan.entries[0].point if plan.entries else None)
            if leg is not None and goal is not None and (not plan.legs or not plan.legs[-1].aside) \
                    and goal in self.graph.points and self.graph.points[goal].zone == leg.destination:
                point = self.graph.points[goal]
                if point.kind == "slot":
                    if point.slot == 1:
                        self.slot_choice.pop(leg, None)
                    else:
                        self.slot_choice[leg] = point.slot
        self.plan_info = {"at": now, "ms": round(result.elapsed_ms, 1), "cost": round(result.cost, 2),
                          "method": result.method, "expansions": result.expansions, "reason": reason,
                          "fallback": result.method != "JOINT", "ok": result.ok}
        summary = {r: [self.endpoint_label(leg.target) + (" (비켜서기)" if leg.aside else "") for leg in p.legs]
                   for r, p in self.plans.items()}
        text = f"다시 계획 · {reason} · {result.elapsed_ms:.0f} ms"
        if result.method != "JOINT":
            text += f" · Level 순 계획으로 대신 ({result.reason})"
        self.record("PLAN_UPDATE", now, text, reason_code=reason, plan_ms=round(result.elapsed_ms, 1),
                    cost=round(result.cost, 2), method=result.method, fallback=result.method != "JOINT",
                    expansions=result.expansions, plans=summary)

    def _robot_plan(self, robot, events, goal_targets, old):
        graph, planner = self.graph, self.planner
        legs, entries = [], []
        last_key = None
        for t, state in events:
            rid, idx, _ = state
            route = graph.routes[rid]
            standing = idx >= len(route.pieces)
            if route.pieces and (not legs or legs[-1].route != rid):
                legs.append(PlanLeg(rid, route.target, route.target not in goal_targets, route.line, route.length))
            leg_no = len(legs) - 1 if route.pieces else (len(legs) - 1 if legs else -1)
            piece = planner.piece_of(state)
            key = (rid, idx if not standing else "stand")
            if key == last_key:
                continue
            last_key = key
            if standing:
                s0 = s1 = route.length if route.pieces else 0.0
            else:
                s0 = route.starts[idx]
                s1 = route.starts[idx + 1] if idx + 1 < len(route.pieces) else route.length
            if entries:
                entries[-1].t1 = t
            entries.append(Entry(piece, graph.pieces[piece].point, leg_no, s0, s1, t, 10 ** 9, standing))
        plan = RobotPlan(legs, entries)
        if old is not None and old.started and legs and old.legs and old.legs[0].target == legs[0].target \
                and self._moving(robot):
            plan.started = True
        if self._moving(robot) and legs and self._move_endpoint(robot) == legs[0].target:
            plan.started = True
        return plan

    def _dependencies(self):
        conflict = self.graph.conflict
        robots = list(self.plans)
        for robot in robots:
            self.plans[robot].deps = {}
        for a in robots:
            for b in robots:
                if a == b:
                    continue
                pa, pb = self.plans[a], self.plans[b]
                for j, eb in enumerate(pb.entries):
                    best = None
                    for i, ea in enumerate(pa.entries):
                        if ea.t1 <= eb.t0 and conflict(ea.piece, eb.piece):
                            best = i
                    if best is not None:
                        pb.deps.setdefault(j, []).append((a, best))

    # --- execution ------------------------------------------------------------------------------

    def _cleared(self, robot, index, now):
        plan = self.plans.get(robot)
        if plan is None:
            return True
        entry = plan.entries[index]
        if entry.leg > 0:
            return False                       # after this robot's next MOVE: wait for the next plan
        if plan.entry <= index:
            return False
        report = self.reports.get(robot)
        if report is None or report.pose is None:
            return False
        line = self.graph.pieces[entry.piece].line
        if len(line) == 1:
            far = dist(line[0], report.pose)
        else:
            far = min(_seg_dist(report.pose, a, b) for a, b in zip(line, line[1:]))
        key = (robot, index)
        if far <= self.config.resources.clear_margin_m:
            self._clear_since.pop(key, None)
            return False
        since = self._clear_since.setdefault(key, now)
        return now - since >= self.config.resources.clear_hold_s

    def _gate(self, robot, plan, now):
        """None when the robot may go on, else (entry index, waiting for robot, its entry).

        Positions starting within the stop line (stop_margin_m, plus one plan step of travel so that a
        robot held here is still outside the stop line when the next plan starts) must be cleared.
        """
        reach = self.config.traffic.stop_margin_m + self.graph.step_m
        s = plan.s if plan.started else 0.0
        for j, entry in enumerate(plan.entries):
            if entry.leg != 0 or (plan.started and j <= plan.entry):
                continue
            if entry.s0 > s + reach:
                break
            for other, i in plan.deps.get(j, ()):
                if not self._cleared(other, i, now) or self._still_holds(other, i, entry.piece):
                    return j, other, i
            other = self._held_by_other(robot, entry.piece)
            if other is not None:
                return j, other, None
        return None

    def _held_by_other_segment(self, robot, name, direction):
        seg = self.segments.get(name)
        if seg is None:
            return None
        for other, holding in seg.holders.items():
            if other != robot and (seg.kind == "single" or other in seg.uncertain or not direction
                                   or not holding.direction or holding.direction != direction):
                return other
        return None

    def _held_by_other(self, robot, piece):
        """Another robot still holds (or is cleared into) a capacity segment of this position: a segment is
        free only after its holder has passed it (clear_margin_m for clear_hold_s), as on the segment map."""
        for name, direction in self.graph.segs(piece).items():
            seg = self.segments.get(name)
            if seg is None:
                continue
            single = seg.kind == "single"
            for other, holding in seg.holders.items():
                if other == robot:
                    continue
                if single or other in seg.uncertain or not direction or not holding.direction \
                        or holding.direction != direction:
                    return other
        return None

    def _still_holds(self, other, index, piece):
        """The other robot still holds a capacity segment this position shares with its earlier one."""
        mine = self.plans[other].entries[index].piece
        return any(other in self.segments[name].holders for name in self.graph.shares(mine, piece)
                   if name in self.segments)

    def _window(self, robot, plan, gate):
        """Segments the robot may use now: its position and the checked positions before the stop line."""
        reach = self.config.traffic.stop_margin_m + self.graph.step_m
        s = plan.s if plan.started else 0.0
        stop = gate[0] if gate else len(plan.entries)
        out = {}
        for j, entry in enumerate(plan.entries[:stop]):
            if entry.leg > 0 or j < plan.entry or entry.leg < 0 or (not plan.started and gate is not None):
                continue
            if j == plan.entry:
                # The piece the robot drives now: keep what it will cross in it, unless another robot still
                # holds that segment (then the board's OCCUPIED, from the real position, is what counts).
                if plan.started:
                    for name, direction in self.graph.segs(entry.piece).items():
                        if not self._held_by_other_segment(robot, name, direction):
                            out.setdefault(name, direction)
                continue
            if entry.leg == 0 and j > plan.entry and entry.s0 > s + reach:
                break
            for name, direction in self.graph.segs(entry.piece).items():
                out.setdefault(name, direction)
        return out

    def _hold(self, robot, gate, now, mission_id):
        if gate is None:
            j, other, reason = 0, None, "계획을 찾지 못해 제자리 대기 · 다시 계획을 기다립니다"
        else:
            j, other, _ = gate
            reason = None
        plan = self.plans[robot]
        point = self.graph.label(plan.entries[j].point)
        reason = reason or f"{point}: {self.robot_label(other)}이 지나간 뒤"
        old = self.held.get(robot)
        self.held[robot] = {"reason": reason, "peer": other, "point": plan.entries[j].point, "since": old["since"] if old else now}
        if not old or old["reason"] != reason:
            self.record("PLAN_WAIT", now, reason, robot=robot, mission_id=mission_id, peer=other,
                        point=plan.entries[j].point)
        self.wait_reasons[robot] = reason

    def _late(self, now):
        step_s = self.gcfg.plan_step_s
        start = self.plan_info.get("at", now)
        for robot, plan in self.plans.items():
            if plan.failed and now - start > self.gcfg.replan_delay_s:
                return robot
            nxt = plan.entry + 1
            if nxt < len(plan.entries) and plan.entries[nxt].leg == 0:
                due = start + plan.entries[nxt].t0 * step_s
                if now > due + self.gcfg.replan_delay_s:
                    return robot
        return None

    def arbitrate(self, now, destination_held=None) -> PlanTick:
        del destination_held
        if self._finishing is not None:
            self._finishing = None
        if self.dirty is None and now - self._late_replan >= self.gcfg.replan_delay_s:
            late = self._late(now)
            if late is not None:
                self._late_replan = now
                self._mark(f"{self.robot_label(late)} 계획보다 늦음")
        if self.dirty is not None:
            self.replan(now, self.dirty)
        result = PlanTick([], [])
        waits = {}
        for robot in self.config.robots:
            plan = self.plans.get(robot)
            leg = self.active.get(robot) or self.pending.get(robot)
            was_held = robot in self.held
            if plan is None:
                self.held.pop(robot, None)
                report = self.reports.get(robot)
                if report is not None and report.link_ok and not self._coasting(robot):
                    # Offline robots keep their segments (UNKNOWN), and so does a robot that may still drive
                    # because its CANCEL has not been confirmed.
                    self.board.grant(robot, {}, now)
                continue
            if plan.failed:
                # No route this time (e.g. the other robot stands in the way while cancelling): keep still.
                self.board.grant(robot, {}, now)
                self._hold(robot, None, now, leg.mission_id if leg else None)
                continue
            if not plan.legs:
                self.held.pop(robot, None)
                self.board.grant(robot, self._window(robot, plan, None), now)
                if robot in self.pending and not self._moving(robot):
                    # Already standing in its goal slot: arrive there without a MOVE (a MOVE would make the
                    # robot "driving" for the proximity rule while it stands still).
                    point = self.graph.points.get(plan.entries[0].point) if plan.entries else None
                    leg = self.pending[robot]
                    if point is not None and point.kind == "slot" and point.zone == leg.destination:
                        self._grant(robot, now)
                        result.arrived.append((robot, point.zone, point.slot))
                    else:
                        result.grants.append(self._grant(robot, now))
                continue
            first = plan.legs[0]
            moving = self._moving(robot)
            current = self._move_endpoint(robot) if moving else None
            if moving and leg is not None and robot in self.aside_moves:
                current = None      # a mission arrived during a move aside: stop it and plan the new goal
            if moving and current != first.target:
                # The plan sends the robot elsewhere: stop this MOVE first (CANCEL), then plan again.
                # Its segments stay held until it has stopped.
                self.held.pop(robot, None)
                if self._retarget_sent.get(robot) is not self.links[robot].move:
                    self._retarget_sent[robot] = self.links[robot].move
                    if leg is not None:
                        result.retarget.append(robot)
                    else:
                        self.links[robot].cancel()
                continue
            gate = self._gate(robot, plan, now)
            self.board.grant(robot, self._window(robot, plan, gate), now)
            if gate is not None:
                self._hold(robot, gate, now, leg.mission_id if leg else None)
                if robot in self.pending:
                    waits[robot] = gate[1]
                continue
            if was_held:
                self.held.pop(robot, None)
                self.wait_reasons.pop(robot, None)
                if moving:
                    result.commands.append(MotionRequest(robot, "RESUME"))
            if not plan.started and not moving:
                if robot in self.pending:
                    granted = self._grant(robot, now)
                    if granted is not None:
                        result.grants.append(granted)
                        plan.started = True
                elif leg is None and robot not in self.active:
                    self._idle_move(robot, first, now)
                    plan.started = True
            elif not plan.started and moving and current == first.target:
                plan.started = True
        self.resources = dict(self.segments)
        for robot, peer in waits.items():
            self.resources[f"PLAN_WAIT:{robot}"] = _WaitView([peer])
        result.grants = [leg for leg in result.grants if leg is not None]
        return result

    def _grant(self, robot, now):
        leg = self.pending.get(robot)
        if leg is None:
            return None
        plan = self.plans.get(robot)
        del self.pending[robot]
        self.active[robot] = leg
        self._origins[robot] = self.regions.get(robot)
        if plan and plan.legs:
            self._last_target[robot] = plan.legs[0].target
        self.wait_reasons.pop(robot, None)
        if plan and plan.legs and plan.legs[0].aside:
            target = plan.legs[0].target
            self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} → {self.endpoint_label(target)} · 길 비켜 주는 중",
                        robot=robot, mission_id=leg.mission_id, target=target)
            self.wait_reasons[robot] = "길 비켜 주는 중"
        return leg

    def _idle_move(self, robot, first, now):
        link = self.links[robot]
        point = self.graph.points[first.target]
        if robot not in self.aside_home:
            here = self.map.nearest_slot(link.pose) if link.pose is not None else None
            self.aside_home[robot] = here
        self._aside_seq += 1
        link.go(f"Y-{self.robot_order[robot]}-{self._aside_seq}", point.zone, 1, slot=point.slot)
        self.aside_moves[robot] = first.target
        self._last_target[robot] = first.target
        if first.aside:
            self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} → {self.endpoint_label(first.target)} · 길 비켜 주는 중",
                        robot=robot, target=first.target)
            self.wait_reasons[robot] = "길 비켜 주는 중"
        else:
            self.wait_reasons.pop(robot, None)

    def tick(self, now, reports):
        self.observe(now, reports)
        return self.arbitrate(now)

    # --- snapshots ------------------------------------------------------------------------------

    def snapshot(self):
        door_b = (self.map.door_names[g.ROOM_B],) if g.ROOM_B in self.map.door_names else ()
        door_e = (self.map.door_names[g.ROOM_E],) if g.ROOM_E in self.map.door_names else ()
        queue = [{"robot": leg.robot, "level": leg.level, "mission_id": leg.mission_id, "since": leg.since}
                 for leg in self.pending.values() if leg.robot in self.held]
        return {
            g.CORRIDOR: self.board.aggregate(self.map.corridor_names, 2, queue),
            g.ROOM_B: self.board.aggregate(door_b, 1),
            g.ROOM_E: self.board.aggregate(door_e, 1),
        }

    def traffic_snapshot(self):
        plans = {}
        for robot, plan in self.plans.items():
            points, route = [], []
            for entry in plan.entries:
                if not points or points[-1] != entry.point:
                    points.append(entry.point)
            for leg in plan.legs:
                route.extend([round(x, 3), round(y, 3)] for x, y in leg.line)
            held = self.held.get(robot)
            plans[robot] = {
                "points": points,
                "labels": [self.graph.label(p) for p in points],
                "route": route,
                "targets": [{"point": leg.target, "label": self.endpoint_label(leg.target), "aside": leg.aside}
                            for leg in plan.legs],
                "wait": held["reason"] if held else None,
                "aside": bool(plan.legs and plan.legs[0].aside) or robot in self.aside_moves,
            }
        stop_waits = {}
        for robot, held in self.held.items():
            stop_waits[robot] = {"segment": held["point"], "segment_label": self.graph.label(held["point"]),
                                 "by": held["peer"] or "", "since": held["since"],
                                 "paused": self._moving(robot), "reason": held["reason"]}
        return {
            "mode": "PLAN",
            "stop_margin_m": self.config.traffic.stop_margin_m,
            "segments": self.board.view(),
            "stop_line_waits": stop_waits,
            "plans": plans,
            "plan": dict(self.plan_info),
        }


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


del time
