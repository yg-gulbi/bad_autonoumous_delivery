"""Single-tick, non-preemptive resource arbitration (BRIEF 5.3)."""

from dataclasses import dataclass, field
from math import dist, inf

from . import geometry as g
from .config import FleetConfig
from .route_planner import RoutePlanner


@dataclass(frozen=True)
class RobotReport:
    pose: g.Point | None
    link_ok: bool
    safe_pause: bool = False
    can_yield: bool = True


@dataclass(frozen=True, eq=False)
class Leg:
    robot: str
    destination: str
    level: int
    since: float
    mission_id: str | None
    carrying_patient: bool
    mission_type: str | None = None


@dataclass
class Holding:
    entered_at: float | None = None
    outside_since: float | None = None
    reserved_next: bool = False

    @property
    def state(self):
        return "GRANTED" if self.entered_at is None else "OCCUPIED"


@dataclass
class Resource:
    capacity: int
    reason: str | None = None
    decided_at: float | None = None
    holders: dict[str, Holding] = field(default_factory=dict)
    uncertain: set[str] = field(default_factory=set)
    blocked_by: str | None = None
    blocked_since: float | None = None
    stall_blockers: set[str] = field(default_factory=set)

    @property
    def holder(self):
        return next(iter(self.holders), None)

    @property
    def state(self):
        if self.uncertain:
            return "UNKNOWN"
        if any(h.state == "OCCUPIED" for h in self.holders.values()):
            return "OCCUPIED"
        return "GRANTED" if self.holders else "FREE"

    def blockers(self, robot):
        if robot in self.holders or len(self.holders) < self.capacity:
            return ()
        return tuple(self.holders)


@dataclass(frozen=True)
class MotionRequest:
    robot: str
    command: str


@dataclass
class TickResult:
    grants: list[Leg]
    commands: list[MotionRequest]


@dataclass
class GiveWay:
    yielding: Leg
    priority: Leg
    since: float
    direction: int  # priority robot's direction along TOP -> BOTTOM
    furthest: float
    last_progress: float


class ResourceManager:
    """Consume complete report sets. A tick returns leg grants and motion requests."""

    def __init__(self, config: FleetConfig):
        self.config = config
        self.planner = RoutePlanner(config)
        self.resources = {name: Resource(1) for name in g.RESOURCES}
        if config.corridor.mode == "SHARED_YIELD":
            self.resources[g.CORRIDOR].capacity = 2
        self.pending: dict[str, Leg] = {}
        self.active: dict[str, Leg] = {}
        self.reports: dict[str, RobotReport] = {}
        self.regions: dict[str, str] = {}
        self._origins: dict[str, str] = {}
        self._finishing: list[Leg] | None = None
        self.give_way: GiveWay | None = None
        self._timed_out_pair: frozenset[Leg] = frozenset()
        self.wait_reasons: dict[str, str] = {}
        self._needed: dict[str, tuple[str, ...]] = {}
        self._decide_after = float("-inf")
        self.events: list[dict] = []
        self.last_decision = None

    def resource_label(self, name):
        if name == g.CORRIDOR:
            return "복도"
        zone = name.removeprefix("ROOM_")
        return self.config.zone_names.get(zone, f"{zone} 구역") + " 출입"

    def record(self, kind, now, reason, *, robot=None, mission_id=None, candidates=None, **detail):
        if candidates is None:
            candidates = [r for r in self.pending if self.eligible(r)]
        self.events.append({
            "v": 1, "ts": now, "type": kind, "robot": robot, "mission_id": mission_id,
            "detail": {"reason": reason, "candidates": candidates, **detail},
        })

        event = self.events[-1]
        names = detail.get("resources", ())
        if kind.startswith("CORRIDOR_YIELD_"):
            names = [g.CORRIDOR]
        if kind in ("RESOURCE_GRANT", "RESOURCE_HOLD", "CORRIDOR_YIELD_START"):
            self.last_decision = {key: event[key] for key in ("type", "ts", "robot", "detail")}
        if kind in ("RESOURCE_GRANT", "RESOURCE_HOLD", "RESOURCE_UNKNOWN", "RESOURCE_RELEASE",
                    "RESOURCE_CLEAR_CONFIRM", "CORRIDOR_YIELD_START", "CORRIDOR_YIELD_END", "CORRIDOR_YIELD_TIMEOUT"):
            for name in names:
                self.resources[name].reason, self.resources[name].decided_at = reason, now

    def request(self, robot, destination, level, now, *, mission_id=None, carrying_patient=False, mission_type=None) -> Leg:
        if robot not in self.config.robots or destination not in self.config.zones:
            raise ValueError("unknown robot or destination")
        if type(level) is not int or not 1 <= level <= 3:
            raise ValueError("level must be an integer from 1 to 3")
        if robot in self.pending or robot in self.active:
            raise ValueError(f"{robot} already has a leg")
        if not self.pending:
            self._decide_after = max(self._decide_after, now + self.config.resources.decision_window_s)
        leg = Leg(robot, destination, level, now, mission_id, carrying_patient, mission_type)
        self.pending[robot] = leg
        self.record("RESOURCE_REQUEST", now, f"{robot}이 {destination}행 구간을 요청했습니다.",
                    robot=robot, mission_id=mission_id, destination=destination, level=level)
        return leg

    def begin_tick(self):
        """Mission transitions can end legs before this tick's pose evidence is reconciled."""
        self._finishing = []

    def finish(self, leg: Leg, now: float) -> bool:
        """End or cancel this specific leg after applying its latest position/link report."""
        robot = leg.robot
        if self.pending.get(robot) is leg:
            del self.pending[robot]
        elif self.active.get(robot) is leg:
            del self.active[robot]
        else:
            return False
        self._needed.pop(robot, None)
        self.wait_reasons.pop(robot, None)
        self._origins.pop(robot, None)
        if self._finishing is None:
            self._return_reservations(leg, now)
        else:
            self._finishing.append(leg)
        return True

    def _return_reservations(self, leg, now):
        robot = leg.robot
        for name, resource in self.resources.items():
            holding = resource.holders.get(robot)
            if holding:
                holding.reserved_next = False
            if holding and holding.state == "GRANTED" and robot not in resource.uncertain:
                self._release(name, robot, now, "구간이 끝나 미진입 허가를 반납했습니다.", leg.mission_id)

    def _release(self, name, robot, now, reason, mission_id=None):
        resource = self.resources[name]
        del resource.holders[robot]
        self.record("RESOURCE_RELEASE", now, reason, robot=robot, mission_id=mission_id, resources=[name])
        if name == g.CORRIDOR and resource.state == "FREE":
            self._decide_after = now + self.config.resources.decision_window_s

    def clear_unknown(self, name: str, by: str, now: float):
        if not isinstance(by, str) or not by.strip():
            raise ValueError("E-400: operator identity required")
        resource = self.resources[name]
        if resource.state != "UNKNOWN":
            raise ValueError("E-409: resource is not UNKNOWN")
        inside = [robot for robot, region in self.regions.items() if region == name]
        confirmed = resource.holders.keys() - resource.uncertain
        if len(confirmed.union(inside)) > resource.capacity:
            raise ValueError("E-423: current reports still exceed capacity")
        reason = f"{by}이 {self.resource_label(name)}의 점유 불명을 확인했습니다."
        self.record("RESOURCE_CLEAR_CONFIRM", now, reason, by=by, resources=[name],
                    uncertain=sorted(resource.uncertain))
        uncertain = resource.uncertain.copy()
        resource.uncertain.clear()
        for robot in list(resource.holders):
            if robot in uncertain and robot not in inside:
                self._release(name, robot, now, reason)
        self._observe(name, inside, now)
        if name == g.CORRIDOR and resource.state == "FREE":
            self._decide_after = now + self.config.resources.decision_window_s

    def _observe(self, name, inside, now):
        resource = self.resources[name]
        policy = self.config.resources
        offline = {r for r in resource.holders if r not in self.reports or not self.reports[r].link_ok}
        if len(resource.holders.keys() | set(inside)) > resource.capacity:
            # Retain confirmed reservations. The additional occupants need reconciliation.
            uncertain = offline | (set(inside) - resource.holders.keys())
            reason = f"{self.resource_label(name)}의 보고·예약 대수가 정원 {resource.capacity}대를 초과했습니다."
        else:
            uncertain = offline
            reason = "자원 보유 로봇의 연결이 끊겼습니다."
        if uncertain - resource.uncertain:
            resource.uncertain.update(uncertain)
            robot = next(iter(uncertain)) if len(uncertain) == 1 else None
            leg = self.active.get(robot)
            self.record("RESOURCE_UNKNOWN", now, reason, robot=robot, mission_id=leg.mission_id if leg else None,
                        code="E-423", resources=[name],
                        observed=list(inside), uncertain=sorted(resource.uncertain))
        # Latch uncertain claims, but continue tracking the other robot's physical occupancy.
        for robot in inside:
            if robot in resource.uncertain:
                continue
            holding = resource.holders.setdefault(robot, Holding())
            holding.reserved_next = False
            if holding.entered_at is None:
                holding.entered_at = now
                leg = self.active.get(robot)
                self.record("RESOURCE_ENTER", now, f"{robot}이 {self.resource_label(name)} 안에 있습니다.",
                            robot=robot, mission_id=leg.mission_id if leg else None, resources=[name])
            holding.outside_since = None
        for robot, holding in list(resource.holders.items()):
            if robot in resource.uncertain:
                holding.outside_since = None
                continue
            if robot in inside or holding.state == "GRANTED":
                continue
            report = self.reports[robot]
            if report.pose is None or report.safe_pause or not self.planner.is_clear(
                name, report.pose, policy.clear_margin_m
            ):
                holding.outside_since = None
                continue
            if holding.outside_since is None:
                holding.outside_since = now
            if now - holding.outside_since >= policy.clear_hold_s:
                if holding.reserved_next:
                    # The outgoing leg cleared, but the returning leg already owns this slot.
                    resource.holders[robot] = Holding()
                    continue
                leg = self.active.get(robot)
                self._release(name, robot, now, f"{robot}이 {self.resource_label(name)} 해제 여유 밖에 연속으로 있습니다.",
                              leg.mission_id if leg else None)

    def effective_level(self, leg: Leg, now: float) -> int:
        interval = self.config.resources.aging_s
        return leg.level + int((now - leg.since) // interval) if interval else leg.level

    def eligible(self, robot: str) -> bool:
        report = self.reports.get(robot)
        return bool(report and report.link_ok and report.pose is not None and not report.safe_pause)

    def needed(self, robot: str) -> tuple[str, ...]:
        return self._needed.get(robot, ())

    def _hold_reason(self, leg, needed, now):
        robot = leg.robot
        if not self.eligible(robot):
            return f"{robot}의 연결·위치 또는 안전 정지 상태로 대기합니다."
        for name in needed:
            resource = self.resources[name]
            if resource.state == "UNKNOWN":
                return f"{self.resource_label(name)} 점유 불명으로 운영자 확인을 기다립니다."
            if resource.stall_blockers - {robot} or (resource.blocked_by is not None and resource.blocked_by != robot):
                return f"{self.resource_label(name)}에 막힘이 보고되어 대기합니다."
            blockers = resource.blockers(robot)
            if blockers:
                return f"{self.resource_label(name)} 정원 {resource.capacity}대를 {', '.join(blockers)}이 사용 중이어서 대기합니다."
        if any(robot not in self.resources[n].holders for n in needed) and now < self._decide_after:
            return "경쟁 요청을 모으는 동안 대기합니다."
        return None

    def observe(self, now: float, reports: dict[str, RobotReport]):
        """Apply the full report set before handling arrivals or cancellations for this tick."""
        self.reports = dict(reports)
        self.regions = {r: self.config.corridor.region_of(s.pose) for r, s in reports.items()
                   if s.link_ok and s.pose is not None}
        for name in self.resources:
            self._observe(name, [r for r, region in self.regions.items() if region == name], now)
        if self._finishing is not None:
            for leg in self._finishing:
                self._return_reservations(leg, now)
            self._finishing = None

    @staticmethod
    def _contenders(legs):
        return [{"robot": leg.robot, "level": leg.level, "mission_id": leg.mission_id,
                 "type": leg.mission_type, "requested_at": leg.since} for leg in legs]

    def _basis(self, leg, needed, queue, requirements, prior_holders, now):
        # Capture the limiting resource before mutating any of its reservations.
        name = next((n for n in needed if self.resources[n].state == "UNKNOWN"
                     or self.resources[n].blocked_by or self.resources[n].stall_blockers
                     or self.resources[n].blockers(leg.robot)), next(iter(needed), None))
        if name is None:
            return {"rule": "ONLY", "state_before": "FREE", "contenders": self._contenders([leg])}
        resource = self.resources[name]
        rivals = [other for other in queue if name in requirements[other.robot] and self.eligible(other.robot)]
        prior = [r for r in prior_holders[name] if r in resource.blockers(leg.robot)]
        rivals.extend(self.active[r] for r in prior if r in self.active and self.active[r] not in rivals)
        if resource.uncertain:
            rule = "UNKNOWN"
        elif resource.blocked_by or resource.stall_blockers:
            rule = "BLOCKED"
        elif prior:
            rule = "NO_PREEMPT" if any(r in self.active and leg.level > self.active[r].level for r in prior) else "CAPACITY"
        elif len(rivals) < 2:
            rule = "ONLY"
        elif self.effective_level(rivals[0], now) != self.effective_level(rivals[1], now):
            rule = "LEVEL"
        elif rivals[0].since != rivals[1].since:
            rule = "REQUEST_TIME"
        else:
            rule = "SUBMIT_ORDER"
        return {"rule": rule, "state_before": resource.state, "contenders": self._contenders(rivals or [leg])}

    def arbitrate(self, now: float) -> TickResult:
        # Python's stable sort preserves submission order even for equal timestamps.
        queue = sorted(self.pending.values(), key=lambda leg: (-self.effective_level(leg, now), leg.since))
        candidates = [leg.robot for leg in queue if self.eligible(leg.robot)]
        ranking = [{"robot": leg.robot, "level": leg.level, "effective_level": self.effective_level(leg, now),
                    "since": leg.since} for leg in queue if leg.robot in candidates]
        for leg in queue:
            report = self.reports.get(leg.robot)
            if report and report.link_ok and report.pose is not None:
                self._needed[leg.robot] = self.planner.required(report.pose, leg.destination)
        requirements = {leg.robot: self.needed(leg.robot) for leg in queue}
        prior_holders = {name: tuple(r.holders) for name, r in self.resources.items()}
        grants = []
        for leg in queue:
            robot = leg.robot
            needed = requirements[robot]
            basis = self._basis(leg, needed, queue, requirements, prior_holders, now)
            reason = self._hold_reason(leg, needed, now)
            if reason:
                if self.wait_reasons.get(robot) != reason:
                    self.record("RESOURCE_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                                candidates=candidates, resources=list(needed), **basis)
                self.wait_reasons[robot] = reason
                continue
            for name in needed:
                resource = self.resources[name]
                holding = resource.holders.setdefault(robot, Holding())
                if holding.state == "OCCUPIED" and self.regions[robot] != name:
                    holding.reserved_next = True
            self._origins[robot] = self.regions[robot]
            self.active[robot] = self.pending.pop(robot)
            self.wait_reasons.pop(robot, None)
            self._needed.pop(robot, None)
            grants.append(leg)
            level = self.effective_level(leg, now)
            self.record("RESOURCE_GRANT", now,
                        f"필요 자원을 모두 사용할 수 있어 실효 Level {level}과 요청 순서에 따라 {robot}에 허가합니다.",
                        robot=robot, mission_id=leg.mission_id, candidates=candidates,
                        resources=list(needed), ranking=ranking, **basis)
        return TickResult(grants, self._corridor_commands(now))

    def tick(self, now: float, reports: dict[str, RobotReport]) -> TickResult:
        self.observe(now, reports)
        return self.arbitrate(now)

    def _projection(self, pose):
        """Signed longitudinal metres from the TOP mouth toward the BOTTOM mouth."""
        top = self.config.corridor.mouths[g.TOP]
        bottom = self.config.corridor.mouths[g.BOTTOM]
        dx, dy = bottom[0] - top[0], bottom[1] - top[1]
        return ((pose[0] - top[0]) * dx + (pose[1] - top[1]) * dy) / dist(top, bottom)

    def _direction(self, leg):
        destination = g.ZONE_REGIONS[leg.destination]
        if destination == g.TOP:
            return -1
        if destination == g.BOTTOM:
            return 1
        if self._origins[leg.robot] in g.ROOMS:
            return 0
        mouth = self.config.corridor.mouths[destination]
        delta = self._projection(mouth) - self._projection(self.reports[leg.robot].pose)
        return (delta > 0) - (delta < 0)

    def _start_give_way(self, now, corridor):
        if len(corridor.holders) != 2 or corridor.uncertain:
            return []
        robots = list(corridor.holders)
        if not all(r in self.active and self.eligible(r) and self.reports[r].can_yield for r in robots):
            return []
        for robot in robots:
            if corridor.holders[robot].entered_at is not None and self.regions[robot] != g.CORRIDOR:
                return []  # already outside, waiting for clearance
        first, second = (self.active[r] for r in robots)
        if self._timed_out_pair == frozenset((first, second)):
            return []
        p, q = (self.reports[r].pose for r in robots)
        policy = self.config.corridor
        if dist(p, q) > policy.yield_trigger_m:
            return []
        direction = self._direction(first)
        if not direction or direction != -self._direction(second):
            return []
        if direction * (self._projection(q) - self._projection(p)) <= 0:
            return []

        def priority(leg):
            entered = corridor.holders[leg.robot].entered_at
            return leg.level, -(entered if entered is not None else inf), leg.robot != "robot_b"

        winner, loser = sorted((first, second), key=priority, reverse=True)
        heading = self._direction(winner)
        progress = heading * self._projection(self.reports[winner.robot].pose)
        self.give_way = GiveWay(loser, winner, now, heading, progress, now)
        labels = self.config.robots
        reason = f"{labels[loser.robot].label} 복도 양보 중 ({labels[winner.robot].label} Level {winner.level})"
        self.wait_reasons[loser.robot] = reason
        self.record("CORRIDOR_YIELD_START", now, reason, robot=loser.robot, mission_id=loser.mission_id,
                    candidates=robots, to=winner.robot, resources=[g.CORRIDOR],
                    state_before=corridor.state, contenders=self._contenders([winner, loser]),
                    rule=("LEVEL" if winner.level != loser.level else
                          "ENTRY_ORDER" if priority(winner)[1] != priority(loser)[1] else "ROBOT_ID"))
        return [MotionRequest(loser.robot, "PAUSE")]

    def _end_give_way(self, now, reason, *, resume=True, timeout=False):
        action = self.give_way
        loser = action.yielding
        self.give_way = None
        if self.active.get(loser.robot) is loser:
            self.wait_reasons.pop(loser.robot, None)
        if timeout:
            self._timed_out_pair = frozenset((loser, action.priority))
        event = "CORRIDOR_YIELD_TIMEOUT" if timeout else "CORRIDOR_YIELD_END"
        self.record(event, now, reason, robot=loser.robot, mission_id=loser.mission_id,
                    candidates=[loser.robot, action.priority.robot], to=action.priority.robot)
        return [MotionRequest(loser.robot, "RESUME")] if resume else []

    def _corridor_commands(self, now):
        policy = self.config.corridor
        if policy.mode != "SHARED_YIELD" or not policy.yield_enabled:
            return []
        corridor = self.resources[g.CORRIDOR]
        action = self.give_way
        if action is None:
            return self._start_give_way(now, corridor)

        loser, winner = action.yielding.robot, action.priority.robot
        yielding_report = self.reports.get(loser)
        if (self.active.get(loser) is not action.yielding
                or (yielding_report and (yielding_report.safe_pause or not yielding_report.can_yield))):
            return self._end_give_way(now, "양보 로봇의 구간 종료 또는 정지로 제어를 넘깁니다.", resume=False)
        if loser not in corridor.holders:
            return self._end_give_way(now, "양보 로봇의 복도 점유가 해제됐습니다.",
                                      resume=self.eligible(loser) and self.regions[loser] != g.CORRIDOR)
        if corridor.uncertain or not self.eligible(loser):
            action.last_progress = now
            return []
        priority_report = self.reports.get(winner)
        if priority_report and not priority_report.can_yield:
            return self._end_give_way(now, "우선 로봇의 구간이 중단되어 양보를 끝냅니다.")
        if winner not in corridor.holders:
            return self._end_give_way(now, "우선 로봇이 복도를 반납했습니다.")
        if (corridor.holders[winner].entered_at is not None and winner in self.regions
                and self.regions[winner] != g.CORRIDOR):
            return self._end_give_way(now, "우선 로봇이 복도를 나갔습니다.")
        if not self.eligible(winner):
            action.last_progress = now
            return []
        p, q = self.reports[winner].pose, yielding_report.pose
        progress = action.direction * self._projection(p)
        if progress > action.direction * self._projection(q) and dist(p, q) >= policy.yield_release_m:
            return self._end_give_way(now, "우선 로봇이 지나가고 해제 거리만큼 벌어졌습니다.")
        if progress > action.furthest:
            action.furthest, action.last_progress = progress, now
        elif now - action.last_progress >= policy.yield_timeout_s:
            return self._end_give_way(now, "우선 로봇의 진행이 없어 복도 양보를 해제합니다.", timeout=True)
        return []

    def robot_state(self, robot, base_state):
        """Overlay resource control on the caller's mission state without modifying it."""
        report = self.reports.get(robot)
        if base_state == "SAFE_PAUSE" or (report and report.safe_pause):
            return "SAFE_PAUSE"
        if base_state == "OFFLINE":
            return base_state
        if self.give_way and self.give_way.yielding.robot == robot:
            return "GIVE_WAY"
        return base_state

    def _give_way_snapshot(self):
        action = self.give_way
        if action:
            holders = self.resources[g.CORRIDOR].holders
            if action.yielding.robot in holders and action.priority.robot in holders:
                return {"yielding": action.yielding.robot, "to": action.priority.robot, "since": action.since}
        return None

    def snapshot(self):
        return {
            name: {"state": resource.state, "capacity": resource.capacity, "holders": list(resource.holders),
                   "holder": resource.holder, "give_way": self._give_way_snapshot() if name == g.CORRIDOR else None,
                   "blocked": bool(resource.stall_blockers) or resource.blocked_by is not None,
                   "blocked_since": resource.blocked_since,
                   "blocked_robots": sorted(resource.stall_blockers | ({resource.blocked_by} if resource.blocked_by else set())),
                   "reason": resource.reason, "decided_at": resource.decided_at,
                   "queue_detail": [{"robot": leg.robot, "level": leg.level, "mission_id": leg.mission_id, "since": leg.since}
                                    for leg in sorted(self.pending.values(), key=lambda l: (-l.level, l.since))
                                    if name in self.needed(leg.robot)],
                   "queue": [{"robot": leg.robot, "level": leg.level, "since": leg.since}
                             for leg in self.pending.values() if name in self.needed(leg.robot)]}
            for name, resource in self.resources.items()
        }
