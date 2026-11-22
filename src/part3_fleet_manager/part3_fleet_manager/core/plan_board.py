"""The plan and the segment board (UNKNOWN included): which segments a robot holds or may use, which ones other
robots still hold, UNKNOWN segments as fences (D-54), and a robot back online settling its own (D-52, D-57)."""

from math import ceil, dist

from .plan_graph import SAMPLE_M, point_at
from .planner import Agent


class PlanBoard:
    """Mixed into PlanManager. ``self.board`` (segments.SegmentBoard) keeps who is inside which segment."""

    # --- who holds what ------------------------------------------------------------------------------------

    def _still_held(self, robot):
        """Capacity segments the board still gives to this robot (not yet released after it left). An UNKNOWN
        segment is part of the robot only while it is offline; back online it is a fence for every robot
        (D-54: the plan must not think that moving this robot frees it)."""
        offline = not self.robots.view(robot).connected
        return tuple((name, seg.holders[robot].direction) for name, seg in self.segments.items()
                     if name in self.graph.capacity and robot in seg.holders
                     and ((seg.holders[robot].state == "OCCUPIED" and robot not in seg.uncertain)
                          or (robot in seg.uncertain and offline)))

    def _held_by_other_segment(self, robot, name, direction):
        """Another robot holds this segment in a way this robot cannot share (single, UNKNOWN, or one_way the
        other way or without a direction)."""
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
            other = self._held_by_other_segment(robot, name, direction)
            if other is not None:
                return other
        return None

    def _still_holds(self, other, index, piece):
        """The other robot still holds a capacity segment this position shares with its earlier one."""
        mine = self.plans[other].entries[index].piece
        return any(other in self.segments[name].holders for name in self.graph.shares(mine, piece)
                   if name in self.segments)

    def _slot_member(self, robot, slot):
        """A slot counts a robot standing there, or driving a position that starts or ends there."""
        if not self.robots.view(robot).driving:
            return self.robots.parked_at.get(robot) == slot
        plan = self.plans.get(robot)
        if plan is None or not plan.entries:
            # No plan right now (just back online, or held still): the slot its MOVE started from or goes to.
            return slot in (self.robots.move_endpoint(robot), self.robots.departed_from.get(robot))
        return slot in self.graph.segs(plan.entries[min(plan.entry, len(plan.entries) - 1)].piece)

    # --- what a robot may use now --------------------------------------------------------------------------

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
                # The piece the robot drives now: keep what it will still cross in it, unless another robot holds
                # that segment (then the board's OCCUPIED, from the real position, is what counts). A segment it
                # has passed in this piece is not given again once the board has released it (random seed 34 of
                # the 4O 정리: the corridor segment behind a robot went back to GRANTED until the next plan).
                if plan.started:
                    for name, direction in self._rest_of_piece(plan, entry).items():
                        if not self._held_by_other_segment(robot, name, direction):
                            out.setdefault(name, direction)
                continue
            if entry.leg == 0 and j > plan.entry and entry.s0 > s + reach:
                break
            for name, direction in self.graph.segs(entry.piece).items():
                out.setdefault(name, direction)
        return out

    def _rest_of_piece(self, plan, entry):
        """Segments of this position from where the robot is now (plan.s) to the end of the position."""
        names = self.graph.segs(entry.piece)
        if not plan.legs or entry.leg != 0 or entry.standing:
            return names
        line = plan.legs[0].line
        s0, s1 = max(plan.s, entry.s0), entry.s1
        steps = max(2, ceil((s1 - s0) / SAMPLE_M))
        samples = [point_at(line, s0 + (s1 - s0) * i / steps) for i in range(steps + 1)]
        out = {}
        for name, direction in names.items():
            capacity = self.graph.capacity.get(name)
            if capacity is None or any(capacity[1].contains(xy) for xy in samples):
                out[name] = direction
        return out

    def _coast_segments(self, robot):
        """Segments the robot holds now plus the capacity segments on the rest of its MOVE that no other robot
        holds (single, or one_way the other way)."""
        names = {name: seg.holders[robot].direction for name, seg in self.segments.items() if robot in seg.holders}
        for piece in self.coast_route.get(robot, ()):
            for name, direction in self.graph.segs(piece).items():
                if name not in names and self._held_by_other_segment(robot, name, direction) is None:
                    names[name] = direction
        return names

    # --- UNKNOWN -------------------------------------------------------------------------------------------

    def _fences(self):
        """UNKNOWN segments as one fixed obstacle that no robot may use (D-54), or () when there are none."""
        pieces = [self.graph.fence(name) for name, seg in self.segments.items()
                  if seg.uncertain and not seg.spec.is_shared]
        if not pieces:
            return ()
        stay = self.graph.stay_route(None, piece=pieces[0])
        return (Agent("UNKNOWN", 0.0, [(stay, None)], {}, static=True, blocks=tuple(pieces[1:])),)

    def _fenced_names(self, agent):
        """UNKNOWN segments on the robot's way to its goal (all of them when its own way is not the problem)."""
        unknown = [name for name, seg in self.segments.items() if seg.uncertain and not seg.spec.is_shared]
        pose = self.robots.view(agent.name).pose
        ahead = []
        if pose is not None and agent.goal_targets:
            rid = self.graph.route_from(pose, agent.goal_targets[0])
            for piece in self.graph.routes[rid].pieces:
                ahead.extend(n for n in self.graph.segs(piece) if n in unknown and n not in ahead)
        return tuple(ahead or unknown)

    def _link_back(self, robot, now, foreign):
        """Back online with a fresh pose (D-52, D-57). Offline the robot drives only along the MOVE it has, and
        it held its segments and the rest of that MOVE (UNKNOWN, D-46·D-49). If the new pose is inside segments
        of its own, they are settled now: it keeps the ones it is inside and the ones it has passed are released.
        Only when the new pose lies in a segment that was not its own (``foreign``) does the operator check them
        on the spot as before."""
        lost_at, before = self.robots.lost.pop(robot)
        names = [n for n, seg in self.segments.items() if robot in seg.uncertain]
        if not names:
            return
        pose = self.robots.view(robot).pose
        lost_s = now - lost_at
        moved_m = dist(pose, before) if before is not None else None
        detail = {"lost_s": round(lost_s, 2), "moved_m": None if moved_m is None else round(moved_m, 2)}
        label = self.robot_label(robot)
        if foreign:
            where = "·".join(self.resource_label(n) for n in foreign)
            self.record("RESOURCE_UNKNOWN", now, f"{label} 연결 복구 · 다시 받은 위치가 이 로봇 몫이 아닌 {where}에 걸쳐 "
                        "현장 확인이 필요합니다", robot=robot, code="E-423", resources=names, observed=[],
                        uncertain=[robot], foreign=list(foreign), **detail)
            return
        self.board.recover(robot, now, f"{label} 연결 {lost_s:.1f}초 만에 복구 · 쥔 칸 안에 있어 지나간 칸 자동 반납",
                           **detail)
        self._mark("연결 복구")

    def clear_unknown(self, name, by, now):
        self.board.clear_unknown(name, by, now)
        self._mark("점유 확인")

    def block_robot(self, robot, pose, now):
        return self.board.block(robot, pose, now)

    def unblock_robot(self, robot):
        """The block is lifted (operator choice, or the robot moved): the robot is in the plan again."""
        self.board.unblock(robot)
        self._mark("막힘 해제")
