"""Robot state layer (4O 정리): what the link and PART 04 say about each robot, in the few words the plan uses.

PART 04 quirks and the link are handled in two places only:

- ``robot_protocol.RobotProtocol`` (the wire): the grace after a MOVE or RESUME until the next STATE (PART 04
  waits up to 5 s for Nav2, D-53), RESUME and CANCEL sent again when unanswered, a CANCEL that meets an idle
  PART 04 ending that MOVE, a COMPLETE STATE standing for a lost ARRIVED (4K) and a FAILED STATE for a lost
  MISSION_FAILED, PAUSE accepted only while navigating.
- this module (the view): ``RobotView`` puts one robot's link and report into plain states — connected (STATE on
  time and a pose), driving (a MOVE out and not over), rolling, paused, stopping, cancelling, coasting (cannot be
  steered but may still drive its MOVE) — and ``RobotStates`` keeps what follows from them over time: link loss
  and recovery (time and pose before the loss, D-52), the slot a robot stands in after a completed MOVE and the
  slot its MOVE started from. PART 04 also drives every MOVE into or out of room B·E through the crossroad, even
  inside one room (``uses_crossroad``; ``plan_graph.door_route`` walks that way).

The plan (plan_*.py) reads robots only through ``RobotStates``: it never looks at a STATE name or a Move.
"""

from dataclasses import dataclass
from math import dist

from . import geometry as g
from .robot_protocol import DONE


def uses_crossroad(origin_region, destination):
    """PART 04 drives through the crossroad into, or out of, room B or E (goal_manager VIA_ROOMS)."""
    end = g.ZONE_REGIONS[destination]
    return origin_region != end and (origin_region in g.ROOMS or end in g.ROOMS)


@dataclass(frozen=True)
class RobotView:
    connected: bool            # STATE on time and a pose this tick
    pose: g.Point | None       # this tick's pose, else the last one the link had (offline: the pose before)
    safe_pause: bool           # stopped by a safety cause
    stalled: bool              # no progress for stall.window_s, or blocked
    driving: bool              # a MOVE went out, is not over and not being cancelled; the robot may still drive it
    rolling: bool              # wheels turning now: STATE MOVING or UNDOCKING (cannot stop before its stop line)
    moving: bool               # STATE MOVING
    paused: bool               # STATE PAUSED
    stopping: bool             # PAUSE sent, PAUSED not reported yet
    cancelling: bool           # CANCEL sent, not confirmed
    coasting: bool             # cannot be steered now (CANCEL unconfirmed or link lost) but has a MOVE it may drive
    still: bool                # STATE IDLE, COMPLETE, CANCELED or FAILED
    completed: bool            # STATE COMPLETE
    open_move: bool            # a MOVE that is not over (sent or not)
    unsent: bool               # a MOVE waiting to go out
    via_done: bool             # past the crossroad on this MOVE (VIA_PASSED, or NAVIGATING without via)
    move_zone: str | None      # zone of the last MOVE, over or not ("DOCK" for a dock approach)
    move_slot: int | None


class RobotStates:
    """Per robot views and what follows from them over time (link loss and recovery, where it stands)."""

    def __init__(self, config, graph, segment_map):
        self.config, self.graph, self.map = config, graph, segment_map
        self.links: dict = {}
        self.reports: dict = {}
        self.parked_at: dict[str, str | None] = {}       # slot the robot stands in after its last MOVE
        self.departed_from: dict[str, str | None] = {}   # slot the robot's current MOVE started from
        self.last_target: dict[str, str | None] = {}     # endpoint of the MOVE we expect it to finish
        self.lost: dict[str, tuple] = {}                 # offline robot -> (time of loss, pose before) (D-52)
        self._connected: dict[str, bool] = {}
        self._safe: dict[str, bool] = {}

    # --- views ---------------------------------------------------------------------------------------------

    def view(self, robot) -> RobotView:
        link = self.links.get(robot)
        report = self.reports.get(robot)
        connected = bool(report and report.link_ok and report.pose is not None)
        pose = report.pose if report is not None and report.pose is not None else (link.pose if link else None)
        move = link.move if link is not None else None
        open_move = move is not None and not move.finished
        cancelling = bool(link is not None and link.cancel_wait)
        state = link.state if link is not None else None
        sent = open_move and bool(move.attempts)
        return RobotView(
            connected=connected, pose=pose,
            safe_pause=bool(report and report.safe_pause), stalled=bool(report and report.stalled),
            driving=sent and not cancelling and (state not in DONE or not move.remote_id),
            rolling=state in ("MOVING", "UNDOCKING"), moving=state == "MOVING", paused=state == "PAUSED",
            stopping=bool(link is not None and link.control_sent == "PAUSE"), cancelling=cancelling,
            coasting=sent and (cancelling or report is None or not report.link_ok),
            still=state in DONE, completed=state == "COMPLETE", open_move=open_move,
            unsent=open_move and not move.attempts,
            via_done=bool(open_move and (move.via_passed or move.via is False)),
            move_zone=move.zone if move is not None else None, move_slot=move.slot if move is not None else None,
        )

    def move_endpoint(self, robot):
        """Graph endpoint of the robot's open MOVE (its dock for a dock approach), or None."""
        view = self.view(robot)
        if not view.open_move:
            return None
        if view.move_zone == "DOCK":
            dock = self.config.robots[robot].dock
            return next((n for n, p in self.graph.endpoints.items() if p.kind == "dock"
                         and dock and p.anchor == (dock.x, dock.y)), None)
        return self.map.slot_names.get((view.move_zone, view.move_slot or 1))

    def move_of(self, robot):
        """The open MOVE itself, only to tell one MOVE from the next (identity)."""
        link = self.links.get(robot)
        return link.move if link is not None else None

    # --- commands the plan gives without a mission (aside moves) -------------------------------------------

    def send_move(self, robot, leg_id, zone, slot):
        self.links[robot].go(leg_id, zone, 1, slot=slot)

    def cancel(self, robot):
        self.links[robot].cancel()

    def forget_aside(self, robot):
        """The aside MOVE is over: the link forgets it (it is not a mission leg)."""
        link = self.links[robot]
        if link.move is not None and link.move.leg_id.startswith("Y-"):
            link.forget()

    # --- over time -----------------------------------------------------------------------------------------

    def observe(self, now, reports):
        """Take this tick's reports. Returns (changes, back): the plan-relevant link and safety changes in the
        order they happened, and the robots back online after a loss."""
        self.reports = dict(reports)
        changes, back = [], []
        for robot, report in reports.items():
            ok = bool(report.link_ok and report.pose is not None)
            if self._connected.get(robot, ok) != ok:
                changes.append("연결 복구" if ok else "연결 끊김")
                if not ok:
                    self.lost[robot] = (now, report.pose)
                elif robot in self.lost:
                    back.append(robot)
            self._connected[robot] = ok
            if self._safe.get(robot) and not report.safe_pause:
                changes.append("안전 정지 해제")
            self._safe[robot] = report.safe_pause
        return changes, back

    def settle_parking(self):
        """A robot stands in a slot when its MOVE to that slot completed (or it started there). A robot
        stopped on the way (cancel, failure) stands at a free position, even inside another slot's circle."""
        for robot, report in self.reports.items():
            if robot not in self.links or report.pose is None:
                continue
            view = self.view(robot)
            if view.driving:
                self.last_target[robot] = self.move_endpoint(robot)
                if self.parked_at.get(robot) is not None:
                    self.departed_from[robot] = self.parked_at[robot]
                self.parked_at[robot] = None
                continue
            self.departed_from.pop(robot, None)
            if robot not in self.parked_at:
                self.parked_at[robot] = self.map.nearest_slot(report.pose)
            if robot in self.last_target and view.still and not view.cancelling and not view.unsent:
                target = self.last_target.pop(robot)
                point = self.graph.points.get(target) if target else None
                arrived = view.completed and point is not None and point.region.contains(report.pose)
                self.parked_at[robot] = target if arrived and point.kind == "slot" else None

    def arrived_at(self, robot, target) -> bool:
        """The robot reported its MOVE complete inside the region of ``target``."""
        view = self.view(robot)
        link = self.links.get(robot)
        return view.completed and link is not None and link.pose is not None and \
            self.graph.points[target].region.contains(link.pose)

    def origin(self, robot, pose):
        """The slot the robot's MOVE started from while it is still inside it, else None."""
        origin = self.departed_from.get(robot)
        if origin is not None and not self.graph.points[origin].region.contains(pose):
            return None
        return origin

    def parked_here(self, robot) -> bool:
        """The robot stands in the slot its last MOVE took it to (``parked_at``; at start-up the slot circle it stands
        in), still inside that slot's circle: parked in its own slot (4L, clearance.close_distance)."""
        here = self.parked_at.get(robot)
        link = self.links.get(robot)
        return (here is not None and link is not None and link.pose is not None
                and self.graph.points[here].region.contains(link.pose))

    def slot_of(self, pose):
        """(zone, slot) whose point is the nearest slot within zone_occupied_m of this pose, else None."""
        if pose is None:
            return None
        config = self.config
        best = min(((dist(pose, point), zone, index) for zone in config.zones
                    for index, point in enumerate(config.slots[zone], 1)), default=None)
        return best[1:] if best and best[0] <= config.resources.zone_occupied_m else None

    def still_at_slot(self, robot, leg_granted) -> bool:
        """Standing still at a slot without a granted leg, online or not: the proximity rule's "still" (4L,
        clearance.stop_distance and close_distance), for collision.py and the plan executor alike."""
        link = self.links.get(robot)
        return self.standing_still(robot, leg_granted) and link is not None and self.slot_of(link.pose) is not None

    def standing_still(self, robot, leg_granted) -> bool:
        """Still for the proximity rule (clearance.stop_distance): no granted leg and STATE done or PAUSED. Online
        or not (4O 정리): a robot that stopped does not start again while its link is down."""
        view = self.view(robot)
        return not leg_granted and (view.still or view.paused)
