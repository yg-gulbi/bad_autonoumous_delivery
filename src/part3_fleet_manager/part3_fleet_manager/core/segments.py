"""Segment map (fleet.yaml traffic.segments/links) and the per-segment occupancy board (Stage 4O).

The map and its geometry come only from fleet.yaml. ``SegmentBoard`` keeps, for the screen and for the
operator's "occupancy unknown" check, which robot is planned through a segment (GRANTED), which robot is
inside (OCCUPIED, the "inside" judgement), when a passed segment is released (clear_margin_m for
clear_hold_s) and which segment lost its holder's link (UNKNOWN). It decides nothing about who goes
first: in PLAN mode that comes from the planner (``plan_traffic``).
"""

from dataclasses import dataclass, field
from math import dist

import networkx as nx
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon

from . import geometry as g
from .config import FleetConfig, SegmentSpec


def _unit_corridor_axis(config: FleetConfig):
    top = config.corridor.mouths[g.TOP]
    bottom = config.corridor.mouths[g.BOTTOM]
    length = dist(top, bottom)
    ux, uy = (bottom[0] - top[0]) / length, (bottom[1] - top[1]) / length
    return top, ux, uy


class Segment:
    """Compiled segment geometry with fast containment, distance, and clearance queries."""

    def __init__(self, spec: SegmentSpec, default_center: g.Point = (0.0, 0.0)):
        self.spec = spec
        self.name = spec.name
        self.kind = spec.kind
        self.label = spec.label
        self._poly = Polygon(spec.polygon) if spec.polygon else None
        self._circle = spec.circle
        if self._circle is not None:
            self.center = (self._circle[0], self._circle[1])
        elif self._poly is not None:
            c = self._poly.centroid
            self.center = (c.x, c.y)
        else:
            self.center = default_center

    @property
    def is_shared(self) -> bool:
        return self.kind == "shared"

    @property
    def is_slot(self) -> bool:
        return self.spec.zone is not None and self.spec.slot is not None

    @property
    def is_narrow(self) -> bool:
        return self.kind in ("one_way", "single") and not self.is_slot

    def contains(self, pose: g.Point, *, include_boundary: bool = True) -> bool:
        if self._circle is not None:
            cx, cy, r = self._circle
            d = dist(pose, (cx, cy))
            return d <= r if include_boundary else d < r
        if self._poly is not None:
            pt = ShapelyPoint(pose)
            return self._poly.covers(pt) if include_boundary else self._poly.contains(pt)
        return False

    def distance(self, pose: g.Point) -> float:
        if self._circle is not None:
            cx, cy, r = self._circle
            return max(0.0, dist(pose, (cx, cy)) - r)
        if self._poly is not None:
            return self._poly.distance(ShapelyPoint(pose))
        return 0.0

    def outside_with_margin(self, pose: g.Point, margin: float) -> bool:
        if self._circle is not None:
            cx, cy, r = self._circle
            return dist(pose, (cx, cy)) > r + margin
        if self._poly is not None:
            return self._poly.distance(ShapelyPoint(pose)) > margin
        return True


class SegmentMap:
    """Graph of hospital segments read from fleet.yaml (no map is built in code)."""

    def __init__(self, config: FleetConfig):
        self.config = config
        self._top, self._ux, self._uy = _unit_corridor_axis(config)
        specs = dict(config.traffic.segments)
        links = tuple(config.traffic.links)
        if not specs or not links:
            raise ValueError("PLAN 모드에는 fleet.yaml의 traffic.segments와 traffic.links가 필요합니다.")
        region_centers = {
            g.TOP: config.corridor.mouths[g.TOP],
            g.BOTTOM: config.corridor.mouths[g.BOTTOM],
            g.ROOM_B: config.zones["B"],
            g.ROOM_E: config.zones["E"],
        }
        self.segments: dict[str, Segment] = {
            name: Segment(spec, region_centers.get(spec.region, (0.0, 0.0)))
            for name, spec in specs.items()
        }
        self.links = links
        self.graph = nx.Graph()
        self.graph.add_nodes_from(self.segments)
        self.graph.add_edges_from(links)
        self.narrow_names = tuple(name for name, seg in self.segments.items() if seg.is_narrow)
        self.corridor_names = tuple(
            name for name, seg in self.segments.items() if seg.is_narrow and seg.spec.room is None
        )
        self.door_names: dict[str, str] = {
            seg.spec.room: name for name, seg in self.segments.items() if seg.is_narrow and seg.spec.room in g.ROOMS
        }
        self.slot_names: dict[tuple[str, int], str] = {
            (seg.spec.zone, seg.spec.slot): name for name, seg in self.segments.items() if seg.is_slot
        }
        self.region_shared: dict[str, str] = {
            seg.spec.region: name for name, seg in self.segments.items() if seg.is_shared and seg.spec.region
        }

    def projection(self, pose: g.Point) -> float:
        """Signed longitudinal metres from TOP mouth toward BOTTOM mouth."""
        return (pose[0] - self._top[0]) * self._ux + (pose[1] - self._top[1]) * self._uy

    def label(self, name: str) -> str:
        seg = self.segments.get(name)
        return seg.label if seg else name

    def nearest_slot(self, pose: g.Point) -> str | None:
        best = None
        for name in self.slot_names.values():
            cx, cy, r = self.segments[name].spec.circle
            d = dist(pose, (cx, cy))
            if d <= r and (best is None or d < best[0]):
                best = (d, name)
        return best[1] if best else None

    def narrow_containing(self, pose: g.Point) -> list[str]:
        inc = self.config.corridor.include_boundary
        return [name for name in self.narrow_names if self.segments[name].contains(pose, include_boundary=inc)]

    def locate(self, pose: g.Point) -> str:
        """Map an arbitrary (x, y) pose to its current segment node in the graph."""
        found = self.narrow_containing(pose)
        if found:
            return found[0]
        slot = self.nearest_slot(pose)
        if slot:
            return slot
        region = self.config.corridor.region_of(pose)
        if region in self.region_shared:
            return self.region_shared[region]
        return min(self.segments, key=lambda n: self.segments[n].distance(pose))


@dataclass
class SegmentHolding:
    entered_at: float | None = None
    outside_since: float | None = None
    direction: int = 0

    @property
    def state(self) -> str:
        return "GRANTED" if self.entered_at is None else "OCCUPIED"


@dataclass
class SegmentState:
    spec: Segment
    holders: dict[str, SegmentHolding] = field(default_factory=dict)
    uncertain: set[str] = field(default_factory=set)
    stall_blockers: set[str] = field(default_factory=set)
    blocked_since: float | None = None
    reason: str | None = None
    decided_at: float | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def holder(self) -> str | None:
        return next(iter(self.holders), None)

    @property
    def state(self) -> str:
        if self.uncertain:
            return "UNKNOWN"
        if any(h.state == "OCCUPIED" for h in self.holders.values()):
            return "OCCUPIED"
        return "GRANTED" if self.holders else "FREE"

    def blockers(self, robot: str) -> tuple[str, ...]:
        if self.kind == "shared":
            return ()
        return tuple(r for r in self.holders if r != robot)


class SegmentBoard:
    """Who is planned through, inside, or unknown in each segment. Used for display and operator checks."""

    def __init__(self, config: FleetConfig, record, robot_label):
        self.config = config
        self.map = SegmentMap(config)
        self.record = record
        self.robot_label = robot_label
        self.segments: dict[str, SegmentState] = {name: SegmentState(seg) for name, seg in self.map.segments.items()}
        self.reports: dict = {}
        self.slot_member = lambda robot, slot: True

    def grant(self, robot: str, names: dict[str, int], now: float):
        """Segments (name -> direction) the robot may use now. Its unentered holdings outside them are returned."""
        for name, seg in self.segments.items():
            if seg.spec.is_shared:
                continue
            holding = seg.holders.get(robot)
            if holding and holding.state == "GRANTED" and name not in names and robot not in seg.uncertain:
                del seg.holders[robot]
                self.record("RESOURCE_RELEASE", now, "계획에서 빠진 미진입 칸을 반납했습니다.", robot=robot,
                            resources=[name])
        for name, direction in names.items():
            seg = self.segments.get(name)
            if seg is None or seg.spec.is_shared:
                continue
            holding = seg.holders.setdefault(robot, SegmentHolding())
            if direction or holding.state == "GRANTED":
                holding.direction = direction

    def inside(self, name: str) -> list[str]:
        seg = self.segments[name]
        inc = self.config.corridor.include_boundary
        found = []
        for robot, report in self.reports.items():
            if not report.link_ok or report.pose is None:
                continue
            if seg.spec.is_slot:
                if self.map.nearest_slot(report.pose) == name and self.slot_member(robot, name):
                    found.append(robot)
            elif seg.spec.contains(report.pose, include_boundary=inc):
                found.append(robot)
        return found

    def observe(self, now: float, reports: dict, slot_member=None):
        self.reports = dict(reports)
        self.slot_member = slot_member or (lambda robot, slot: True)
        policy = self.config.resources
        for name, seg in self.segments.items():
            if seg.spec.is_shared:
                continue
            inside = self.inside(name)
            offline = {r for r in seg.holders
                       if r not in self.reports or not self.reports[r].link_ok or self.reports[r].pose is None}
            if offline - seg.uncertain:
                seg.uncertain.update(offline)
                robot = next(iter(offline)) if len(offline) == 1 else None
                self.record("RESOURCE_UNKNOWN", now, "자원 보유 로봇의 연결이 끊겼습니다.", robot=robot, code="E-423",
                            resources=[name], observed=list(inside), uncertain=sorted(seg.uncertain))
            for robot in inside:
                if robot in seg.uncertain:
                    continue
                holding = seg.holders.setdefault(robot, SegmentHolding())
                if holding.entered_at is None:
                    holding.entered_at = now
                    self.record("RESOURCE_ENTER", now, f"{self.robot_label(robot)} · {seg.spec.label} 진입 확인",
                                robot=robot, resources=[name])
                holding.outside_since = None
            for robot, holding in list(seg.holders.items()):
                if robot in seg.uncertain or robot in inside or holding.state == "GRANTED":
                    holding.outside_since = None
                    continue
                report = self.reports[robot]
                if seg.spec.is_slot:
                    clear = self.map.nearest_slot(report.pose) != name
                else:
                    clear = seg.spec.outside_with_margin(report.pose, policy.clear_margin_m)
                if not clear:
                    holding.outside_since = None
                    continue
                if holding.outside_since is None:
                    holding.outside_since = now
                if now - holding.outside_since >= policy.clear_hold_s:
                    del seg.holders[robot]
                    self.record("RESOURCE_RELEASE", now, f"{self.robot_label(robot)} · {seg.spec.label} 통과 완료",
                                robot=robot, resources=[name])

    def clear_unknown(self, name: str, by: str, now: float):
        if not isinstance(by, str) or not by.strip():
            raise ValueError("E-400: 조작자 이름을 입력하세요.")
        # A specific segment, or the legacy aggregate keys (CORRIDOR, ROOM_B, ROOM_E).
        if name == g.CORRIDOR:
            targets = [n for n in self.map.corridor_names if self.segments[n].state == "UNKNOWN"]
        elif name in self.map.door_names:
            door = self.map.door_names[name]
            targets = [door] if self.segments[door].state == "UNKNOWN" else []
        elif name in self.segments:
            targets = [name] if self.segments[name].state == "UNKNOWN" else []
        else:
            raise ValueError("E-400: 알 수 없는 구간입니다.")
        if not targets:
            raise ValueError("E-409: 점유 확인이 필요한 구간만 해제할 수 있습니다.")
        for seg_name in targets:
            seg = self.segments[seg_name]
            inside = self.inside(seg_name)
            confirmed = seg.holders.keys() - seg.uncertain
            if seg.kind == "single" and len(confirmed.union(inside)) > 1:
                raise ValueError("E-423: 현재 보고된 로봇 수가 정원을 초과합니다.")
        released = []
        for seg_name in targets:
            seg = self.segments[seg_name]
            inside = self.inside(seg_name)
            reason = f"{by} — {seg.spec.label} 현장 확인 완료"
            self.record("RESOURCE_CLEAR_CONFIRM", now, reason, by=by, resources=[seg_name],
                        uncertain=sorted(seg.uncertain))
            for robot in list(seg.uncertain):
                if robot not in inside:
                    seg.holders.pop(robot, None)
                    released.append(robot)
                    self.record("RESOURCE_RELEASE", now, reason, robot=robot, resources=[seg_name])
                elif robot in seg.holders and seg.holders[robot].entered_at is None:
                    # Claimed while offline and found inside: it occupies the segment now.
                    seg.holders[robot].entered_at = now
            seg.uncertain.clear()
        return released

    def recover(self, robot: str, now: float, reason: str, **detail) -> list[str]:
        """The robot is back online at a fresh pose after a short link loss in which it cannot have moved past
        its stop line (D-52): it keeps the UNKNOWN segments it is inside, the others are released without an
        operator check. Segments that are UNKNOWN for another robot as well stay UNKNOWN for that robot."""
        released = []
        for name, seg in self.segments.items():
            if robot not in seg.uncertain:
                continue
            inside = robot in self.inside(name)
            seg.uncertain.discard(robot)
            if inside:
                holding = seg.holders.setdefault(robot, SegmentHolding())
                if holding.entered_at is None:
                    holding.entered_at = now
                    self.record("RESOURCE_ENTER", now, f"{self.robot_label(robot)} · {seg.spec.label} 진입 확인",
                                robot=robot, resources=[name])
                holding.outside_since = None
            else:
                seg.holders.pop(robot, None)
                released.append(name)
                self.record("RESOURCE_RELEASE", now, reason, robot=robot, resources=[name], auto=True, **detail)
        return released

    def block(self, robot: str, pose: g.Point | None, now: float) -> str | None:
        if pose is None:
            return None
        names = self.map.narrow_containing(pose) or [self.map.locate(pose)]
        for name in names:
            seg = self.segments[name]
            if not seg.stall_blockers:
                seg.blocked_since = now
            seg.stall_blockers.add(robot)
        return names[0]

    def unblock(self, robot: str):
        for seg in self.segments.values():
            if robot in seg.stall_blockers:
                seg.stall_blockers.discard(robot)
                if not seg.stall_blockers:
                    seg.blocked_since = None

    def aggregate(self, seg_names, capacity: int, queue=()) -> dict:
        """Legacy-shaped CORRIDOR / ROOM_B / ROOM_E entry for the screen."""
        segs = [self.segments[n] for n in seg_names if n in self.segments]
        holders_map: dict[str, tuple[str, int]] = {}
        for s in segs:
            for r, h in s.holders.items():
                prev = holders_map.get(r)
                st = "OCCUPIED" if h.state == "OCCUPIED" or (prev and prev[0] == "OCCUPIED") else "GRANTED"
                holders_map[r] = (st, h.direction or (prev[1] if prev else 0))
        holders = list(holders_map)[:capacity]
        if any(s.state == "UNKNOWN" for s in segs):
            state = "UNKNOWN"
        elif any(holders_map[r][0] == "OCCUPIED" for r in holders):
            state = "OCCUPIED"
        else:
            state = "GRANTED" if holders else "FREE"
        blocked_robots = sorted({r for s in segs for r in s.stall_blockers})
        blocked_times = [s.blocked_since for s in segs if s.blocked_since is not None]
        decided = max(((s.decided_at, s.reason) for s in segs if s.decided_at is not None), default=(None, None))
        return {
            "state": state, "capacity": capacity, "holders": holders, "holder": holders[0] if holders else None,
            "give_way": None,
            "holder_detail": [{"robot": r, "state": holders_map[r][0], "direction": holders_map[r][1]} for r in holders],
            "follow_stop": None,
            "blocked": bool(blocked_robots), "blocked_since": min(blocked_times) if blocked_times else None,
            "blocked_robots": blocked_robots, "reason": decided[1], "decided_at": decided[0],
            "queue_detail": [dict(item) for item in queue],
            "queue": [{"robot": i["robot"], "level": i["level"], "since": i["since"]} for i in queue],
        }

    def view(self) -> dict:
        out = {}
        for name, seg in self.segments.items():
            if seg.spec.is_shared:
                continue
            dirs = {h.direction for h in seg.holders.values() if h.direction}
            out[name] = {
                "kind": seg.kind, "label": seg.spec.label, "state": seg.state,
                "direction": next(iter(dirs)) if len(dirs) == 1 else 0,
                "holders": [{"robot": r, "state": "UNKNOWN" if r in seg.uncertain else h.state, "direction": h.direction}
                            for r, h in seg.holders.items()],
                "blocked": bool(seg.stall_blockers), "uncertain": sorted(seg.uncertain),
            }
        return out
