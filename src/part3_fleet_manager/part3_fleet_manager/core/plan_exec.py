"""ADG execution: keep the plan's order while the robots drive (Hönig et al. 2019, D-43).

Every robot has a list of positions (``Entry``). For every pair of conflicting positions of two robots the one
planned second depends on the one planned first (``Dep``). Each dependency has a conflict kind, and the kind
alone says when the order counts as kept (``ORDER_KEPT``); nothing else in the code decides that:

    kind     the two ways at the conflicting position
    -------  ------------------------------------------------------------------------------------------
    cross    they cross (or come closer than the proximity distance without sharing the way)
    meet     the same way in opposite directions
    follow   the same way in the same direction, the robot planned second behind (D-59)

A robot whose next position (within the stop line) is not cleared is held before its stop line
(``stop_margin_m``): a MOVE is not sent yet, or the moving robot gets PAUSE and later RESUME.
"""

from dataclasses import dataclass, field
from math import dist, hypot

from .clearance import close_distance, leaving
from .legs import MotionRequest
from .plan_graph import project


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


@dataclass(frozen=True)
class Dep:
    robot: str          # the robot planned first
    entry: int          # its position the dependent position waits for
    kind: str           # CROSS, MEET or FOLLOW


@dataclass
class RobotPlan:
    legs: list
    entries: list
    deps: dict = field(default_factory=dict)        # entry index -> [Dep]
    s: float = 0.0
    entry: int = 0
    started: bool = False                           # MOVE for legs[0] sent
    failed: bool = False                            # no route found: keep still until the next plan


# --- conflict kinds and when the order is kept ------------------------------------------------------------

CROSS, MEET, FOLLOW = "cross", "meet", "follow"
# The robot planned second, at the position that waits: driving toward it, held at its stop line, or still held
# at the stop line of an earlier position of the same way (the order there is kept now; this one is checked next).
DRIVING, WAITING_HERE, WAITING_BEFORE = "driving", "waiting_here", "waiting_before"

# kind -> follower state -> alternatives; the order is kept when every condition of one alternative holds.
#   ahead_left_shared     the robot ahead holds no capacity segment the two positions share any more
#   ahead_passed          it has left its position: clear_margin_m away for clear_hold_s
#   ahead_drives          it is online and driving (STATE MOVING, no PAUSE sent, not held, not stalled or stopped)
#                         on its first MOVE, at or past that position
#   gap_follow            the two robots are at least corridor.follow_gap_m apart
#   gap_resume            ... at least corridor.follow_resume_m (a robot that waits starts again only then)
#   ahead_not_in_single   the robot ahead is not inside a one-robot segment (crossroad, door, slot) on the way of
#                         the one that waits: it would only drive up to it and stop again
_PASSED = ("ahead_left_shared", "ahead_passed")
ORDER_KEPT = {
    CROSS: {DRIVING: (_PASSED,), WAITING_HERE: (_PASSED,), WAITING_BEFORE: (_PASSED,)},
    MEET: {DRIVING: (_PASSED,), WAITING_HERE: (_PASSED,), WAITING_BEFORE: (_PASSED,)},
    FOLLOW: {
        DRIVING: (_PASSED, ("ahead_left_shared", "ahead_drives", "gap_follow")),
        WAITING_HERE: (_PASSED, ("ahead_left_shared", "ahead_drives", "ahead_not_in_single", "gap_resume")),
        WAITING_BEFORE: (_PASSED, ("ahead_left_shared", "ahead_drives", "gap_resume")),
    },
}


def conflict_kind(ahead, behind):
    """Kind of the conflict between the entries of the robot planned first (from its conflicting position on)
    and those of the robot planned second (from its waiting position on)."""
    if _follows(ahead, behind):
        return FOLLOW
    if _meets(ahead, behind):
        return MEET
    return CROSS


def _follows(ahead, behind):
    """The entries behind go the way of the entries ahead, in the same direction: the position ahead and the next one
    come in the same order on the way behind. Standing positions do not count (the robot ahead stops there)."""
    if not ahead or not behind or ahead[0].standing or behind[0].standing:
        return False
    way_ahead = _points(ahead)
    way_behind = _points(behind)
    if len(way_ahead) < 2:
        return False
    return any(way_behind[k] == way_ahead[0] and way_behind[k + 1] == way_ahead[1] for k in range(len(way_behind) - 1))


def _meets(ahead, behind):
    """The same way in opposite directions: the position ahead and the next one come in reverse order behind."""
    way_ahead, way_behind = _points(ahead), _points(behind)
    if len(way_ahead) < 2:
        return False
    return any(way_behind[k] == way_ahead[1] and way_behind[k + 1] == way_ahead[0] for k in range(len(way_behind) - 1))


def _points(entries):
    out = []
    for entry in entries:
        if not out or out[-1] != entry.point:
            out.append(entry.point)
    return out


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


class PlanExec:
    """Mixed into PlanManager: runs the current plan every tick."""

    # --- progress along the plan ---------------------------------------------------------------------------

    def _on_first_leg(self, robot, plan):
        return plan.started and self.robots.view(robot).driving and self.robots.move_endpoint(robot) == plan.legs[0].target

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

    # --- the order of one dependency -----------------------------------------------------------------------

    def _follower_state(self, robot, entry):
        held = self.held.get(robot)
        if held is None:
            return DRIVING
        return WAITING_HERE if held["point"] == entry.point else WAITING_BEFORE

    def _order_kept(self, robot, j, dep, now):
        """True when robot's entry j may go on as far as dependency ``dep`` goes (the table above)."""
        plan = self.plans[robot]
        entry = plan.entries[j]
        known = {}

        def holds(name):
            if name not in known:
                known[name] = self._CONDITIONS[name](self, robot, j, dep, entry, now)
            return known[name]

        rows = ORDER_KEPT[dep.kind][self._follower_state(robot, entry)]
        return any(all(holds(name) for name in row) for row in rows)

    def _c_ahead_left_shared(self, robot, j, dep, entry, now):
        return not self._still_holds(dep.robot, dep.entry, entry.piece)

    def _c_ahead_passed(self, robot, j, dep, entry, now):
        return self._cleared(dep.robot, dep.entry, now)

    def _c_ahead_drives(self, robot, j, dep, entry, now):
        other, ahead = dep.robot, self.plans.get(dep.robot)
        view, mine = self.robots.view(other), self.reports.get(robot)
        if ahead is None or mine is None or mine.pose is None or not view.connected or view.safe_pause \
                or view.stalled or view.cancelling or not view.moving or view.stopping or other in self.held:
            return False
        return self._on_first_leg(other, ahead) and ahead.entries[dep.entry].leg == 0 and ahead.entry >= dep.entry

    def _c_ahead_not_in_single(self, robot, j, dep, entry, now):
        for later in self.plans[robot].entries[j:]:
            if later.leg != 0:
                break
            for name in self.graph.segs(later.piece):
                seg = self.segments.get(name)
                holding = seg.holders.get(dep.robot) if seg is not None else None
                if holding is not None and seg.kind == "single" and holding.state == "OCCUPIED":
                    return False
        return True

    def _gap(self, robot, dep):
        return dist(self.reports[dep.robot].pose, self.reports[robot].pose)

    def _c_gap_follow(self, robot, j, dep, entry, now):
        return self._gap(robot, dep) >= self.config.corridor.follow_gap_m

    def _c_gap_resume(self, robot, j, dep, entry, now):
        return self._gap(robot, dep) >= self.config.corridor.follow_resume_m

    _CONDITIONS = {
        "ahead_left_shared": _c_ahead_left_shared,
        "ahead_passed": _c_ahead_passed,
        "ahead_drives": _c_ahead_drives,
        "ahead_not_in_single": _c_ahead_not_in_single,
        "gap_follow": _c_gap_follow,
        "gap_resume": _c_gap_resume,
    }

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
        close = self._too_close(robot)
        for j, entry in enumerate(plan.entries):
            if entry.leg != 0 or (plan.started and j <= plan.entry):
                continue
            if entry.s0 > s + reach:
                break
            for other in close:
                if not self._moving_apart(robot, other, entry.piece) and self._leaves_next(other, robot, now):
                    return j, other, None       # too close: the other one moves apart first, this one closes in
            for dep in plan.deps.get(j, ()):
                if not self._order_kept(robot, j, dep, now):
                    return j, dep.robot, dep.entry
            other = self._held_by_other(robot, entry.piece)
            if other is not None and not (self._moving_apart(robot, other, entry.piece)
                                          or self._leaving_standing(robot, other, entry.piece)):
                return j, other, None
        return None

    def _too_close(self, robot):
        """Other robots already closer to this one than the stop distance (they stood there, or were put there)."""
        mine = self.reports.get(robot)
        if mine is None or mine.pose is None:
            return ()
        return tuple(other for other, report in self.reports.items() if other != robot and report.pose is not None
                     and dist(mine.pose, report.pose) < self._close_limit(robot, other))

    def _close_limit(self, robot, other):
        """clearance.close_distance: parked in its own slot (4L), as collision.py."""
        return close_distance(self.config.safety, self.robots.parked_here(robot), self.robots.parked_here(other))

    def _leaves_next(self, robot, other, now):
        """The robot can go on now to a next position (on its current MOVE, or its first MOVE) that does not come
        closer to ``other``. When both robots can only close in, or the plan's order has this one wait for ``other``
        first (hall face-off, 4O 후속 6), neither waits for the other here: the plan's order decides."""
        plan = self.plans.get(robot)
        if plan is None or plan.failed or not plan.legs:
            return False
        for j, e in enumerate(plan.entries):
            if e.leg != 0 or (plan.started and j <= plan.entry):
                continue
            if any(dep.robot == other and not self._order_kept(robot, j, dep, now) for dep in plan.deps.get(j, ())):
                return False
            if not e.standing:
                return self._moving_apart(robot, other, e.piece)
        return False

    def _leaving_standing(self, robot, other, piece):
        """The other robot only stands in a one_way segment of this position (no direction, link up), and this
        position does not come closer to it: the planner's ``grace`` for robots that start in one segment
        (plan_graph.close). Two robots standing 0.61 m apart in CORRIDOR_BOTTOM held each other up for ever
        (random seed 198 of 4O 후속 6)."""
        mine, theirs = self.reports.get(robot), self.reports.get(other)
        if mine is None or theirs is None or mine.pose is None or theirs.pose is None:
            return False
        if self.robots.view(other).driving:
            return False
        for name in self.graph.segs(piece):
            seg = self.segments.get(name)
            holding = seg.holders.get(other) if seg is not None else None
            if holding is None:
                continue
            if seg.kind != "one_way" or holding.direction or other in seg.uncertain:
                return False
        return leaving(dist(mine.pose, theirs.pose), self._near(piece, theirs.pose))

    def _near(self, piece, xy):
        line = self.graph.pieces[piece].line
        return dist(line[0], xy) if len(line) == 1 else min(_seg_dist(xy, a, b) for a, b in zip(line, line[1:]))

    def _moving_apart(self, robot, other, piece):
        """The two robots are already closer than the stop distance (the planner's ``grace``): a position of this
        robot that does not come closer to the other one than they are now is not held up by the other's segment
        (two robots standing in one corridor segment would hold each other up for ever, 4O 후속 6)."""
        mine, theirs = self.reports.get(robot), self.reports.get(other)
        if mine is None or theirs is None or mine.pose is None or theirs.pose is None:
            return False
        now_m = dist(mine.pose, theirs.pose)
        if now_m >= self._close_limit(robot, other):
            return False
        return leaving(now_m, self._near(piece, theirs.pose))

    # --- holding, letting go -------------------------------------------------------------------------------

    def _hold(self, robot, gate, now, mission_id):
        if gate is None and robot in self.fenced:
            names = "·".join(self.resource_label(n) for n in self.fenced[robot])
            j, other, reason = 0, None, f"{names}: 현장 확인 필요"
        elif gate is None:
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

    def execute(self, now, result, *, starts=True):
        """One tick of the current plan. ``starts`` False while a new plan is being made (plan_runner): robots
        on their way keep the order of the plan they have, but no robot starts a MOVE, turns or goes on (RESUME)
        until the new plan is in."""
        # Robots whose MOVE (new or resent) or RESUME the plan's executor lets out in this tick (D-55). Every
        # MOVE and RESUME to PART 04 needs this, whoever asked for it (operator resume, card choice, retry).
        self.go = set()
        for robot in self.config.robots:
            plan = self.plans.get(robot)
            leg = self.active.get(robot) or self.pending.get(robot)
            view = self.robots.view(robot)
            was_held = robot in self.held
            if plan is None:
                self.held.pop(robot, None)
                report = self.reports.get(robot)
                if report is not None and report.link_ok and not view.coasting:
                    # Offline robots keep their segments (UNKNOWN), and so does a robot that may still drive
                    # because its CANCEL has not been confirmed.
                    self.board.grant(robot, {}, now)
                elif (report is None or not report.link_ok) and view.coasting and robot in self.coast_route:
                    # Link lost while driving: it may go on along its MOVE, so it also holds the free segments on
                    # that way (UNKNOWN while offline); the plan already keeps the other robot off it (D-46).
                    self.board.grant(robot, self._coast_segments(robot), now)
                continue
            if plan.failed:
                # No route this time (e.g. the other robot stands in the way while cancelling): keep still.
                self.board.grant(robot, {}, now)
                self._hold(robot, None, now, leg.mission_id if leg else None)
                continue
            if not plan.legs:
                self.held.pop(robot, None)
                self.board.grant(robot, self._window(robot, plan, None), now)
                if starts and robot in self.pending and not view.driving:
                    # Already standing in its goal slot: arrive there without a MOVE (a MOVE would make the
                    # robot "driving" for the proximity rule while it stands still).
                    point = self.graph.points.get(plan.entries[0].point) if plan.entries else None
                    leg = self.pending[robot]
                    if point is not None and point.kind == "slot" and point.zone == leg.destination:
                        self._grant(robot, now)
                        result.arrived.append((robot, point.zone, point.slot))
                    else:
                        result.grants.append(self._grant(robot, now))
                        self.go.add(robot)
                continue
            first = plan.legs[0]
            current = self.robots.move_endpoint(robot) if view.driving else None
            if view.driving and leg is not None and robot in self.aside_moves:
                current = None      # a mission arrived during a move aside: stop it and plan the new goal
            if view.driving and current != first.target:
                # The plan sends the robot elsewhere: stop this MOVE first (CANCEL), then plan again.
                # Its segments stay held until it has stopped.
                self.held.pop(robot, None)
                if starts and self._retarget_sent.get(robot) is not self.robots.move_of(robot):
                    self._retarget_sent[robot] = self.robots.move_of(robot)
                    if leg is not None:
                        result.retarget.append(robot)
                    else:
                        self.robots.cancel(robot)
                continue
            gate = self._gate(robot, plan, now)
            self.board.grant(robot, self._window(robot, plan, gate), now)
            if gate is not None:
                self._hold(robot, gate, now, leg.mission_id if leg else None)
                continue
            if not starts:
                continue            # cleared, but it waits for the new plan (no MOVE, RESUME or turn now)
            self.go.add(robot)
            if was_held:
                self.held.pop(robot, None)
                self.wait_reasons.pop(robot, None)
                if view.driving:
                    result.commands.append(MotionRequest(robot, "RESUME"))
            if not plan.started and not view.driving:
                if robot in self.pending:
                    granted = self._grant(robot, now)
                    if granted is not None:
                        result.grants.append(granted)
                        plan.started = True
                elif leg is None and robot not in self.active:
                    self._idle_move(robot, first, now)
                    plan.started = True
            elif not plan.started and view.driving and current == first.target:
                plan.started = True
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
            self.robots.last_target[robot] = plan.legs[0].target
        self.wait_reasons.pop(robot, None)
        if plan and plan.legs and plan.legs[0].aside:
            target = plan.legs[0].target
            self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} → {self.endpoint_label(target)} · 길 비켜 주는 중",
                        robot=robot, mission_id=leg.mission_id, target=target)
            self.wait_reasons[robot] = "길 비켜 주는 중"
        return leg

    # --- moves without a mission (out of the way, and back) ------------------------------------------------

    def _idle_move(self, robot, first, now):
        point = self.graph.points[first.target]
        if robot not in self.aside_home:
            pose = self.robots.links[robot].pose
            self.aside_home[robot] = self.map.nearest_slot(pose) if pose is not None else None
        self._aside_seq += 1
        self.robots.send_move(robot, f"Y-{self.robot_order[robot]}-{self._aside_seq}", point.zone, point.slot)
        self.aside_moves[robot] = first.target
        self.robots.last_target[robot] = first.target
        if first.aside:
            self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} → {self.endpoint_label(first.target)} · 길 비켜 주는 중",
                        robot=robot, target=first.target)
            self.wait_reasons[robot] = "길 비켜 주는 중"
        else:
            self.wait_reasons.pop(robot, None)

    def _idle_arrivals(self, now):
        """End of a MOVE we sent to a robot without a mission (aside, or back to its spot)."""
        for robot, target in list(self.aside_moves.items()):
            view = self.robots.view(robot)
            if robot not in self.robots.links or view.cancelling or view.open_move:
                continue
            arrived = self.robots.arrived_at(robot, target)
            self.robots.forget_aside(robot)
            del self.aside_moves[robot]
            self.robots.last_target.pop(robot, None)
            self.robots.parked_at[robot] = target if arrived else None
            if arrived and self.aside_home.get(robot) == target:
                self.aside_home.pop(robot, None)
                self._mark("자리로 돌아옴")
            elif arrived:
                self.record("PLAN_YIELD_MOVE", now, f"{self.robot_label(robot)} · {self.endpoint_label(target)} 도착, 길 비켜 주는 중",
                            robot=robot, target=target, arrived=True)
                self._mark("비켜선 자리 도착")
            else:
                self._mark("비켜서기 이동 멈춤")
