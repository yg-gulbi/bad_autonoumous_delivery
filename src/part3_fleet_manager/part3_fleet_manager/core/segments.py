"""Segment map geometry and single-rule segment traffic arbitration (Stage 4O)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import dist

import networkx as nx
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon

from . import geometry as g
from .config import FleetConfig, SegmentSpec
from .resource_manager import FollowStop, Leg, MotionRequest, RobotReport, TickResult


def _unit_corridor_axis(config: FleetConfig):
    top = config.corridor.mouths[g.TOP]
    bottom = config.corridor.mouths[g.BOTTOM]
    length = dist(top, bottom)
    ux, uy = (bottom[0] - top[0]) / length, (bottom[1] - top[1]) / length
    return top, ux, uy


def build_default_map_specs(config: FleetConfig) -> tuple[dict[str, SegmentSpec], tuple[tuple[str, str], ...]]:
    """Derive the segment map and connectivity graph from fleet.yaml corridor/zones."""
    tl, tr, br, bl = config.corridor.polygon
    top, ux, uy = _unit_corridor_axis(config)

    def proj(point):
        return (point[0] - top[0]) * ux + (point[1] - top[1]) * uy

    def at_s(a, b, s):
        sa, sb = proj(a), proj(b)
        t = (s - sa) / (sb - sa)
        return (round(a[0] + t * (b[0] - a[0]), 3), round(a[1] + t * (b[1] - a[1]), 3))

    cr = config.corridor.crossroad or ((tl[0] + tr[0] + br[0] + bl[0]) / 4, (tl[1] + tr[1] + br[1] + bl[1]) / 4)
    cr_r = config.corridor.crossroad_clear_m
    cr_s = proj(cr)
    s_hi, s_lo = cr_s - cr_r, cr_s + cr_r
    tl_hi, tr_hi = at_s(tl, bl, s_hi), at_s(tr, br, s_hi)
    bl_lo, br_lo = at_s(tl, bl, s_lo), at_s(tr, br, s_lo)

    nx_l, ny_l = uy, -ux
    if (config.zones["B"][0] - cr[0]) * nx_l + (config.zones["B"][1] - cr[1]) * ny_l < 0:
        nx_l, ny_l = -nx_l, -ny_l
    nx_r, ny_r = -nx_l, -ny_l
    depth = max(config.resources.clear_margin_m, 0.20)

    def zone_name(zone):
        return config.zone_names.get(zone) or f"{zone} 구역"

    segments = {
        "HALL_TOP": SegmentSpec("HALL_TOP", "shared", "위쪽 홀", region=g.TOP),
        "HALL_BOTTOM": SegmentSpec("HALL_BOTTOM", "shared", "아래쪽 홀", region=g.BOTTOM),
        "ROOM_B": SegmentSpec("ROOM_B", "shared", zone_name("B"), region=g.ROOM_B, room=g.ROOM_B),
        "ROOM_E": SegmentSpec("ROOM_E", "shared", zone_name("E"), region=g.ROOM_E, room=g.ROOM_E),
        "CORRIDOR_TOP": SegmentSpec("CORRIDOR_TOP", "one_way", "복도 위쪽", polygon=(tl, tr, tr_hi, tl_hi)),
        "CROSSROAD": SegmentSpec("CROSSROAD", "single", "교차로", circle=(cr[0], cr[1], cr_r)),
        "CORRIDOR_BOTTOM": SegmentSpec("CORRIDOR_BOTTOM", "one_way", "복도 아래쪽", polygon=(bl_lo, br_lo, br, bl)),
        "DOOR_B": SegmentSpec(
            "DOOR_B", "single", f"{zone_name('B')} 문", room=g.ROOM_B,
            polygon=(tl_hi, bl_lo,
                     (round(bl_lo[0] + depth * nx_l, 3), round(bl_lo[1] + depth * ny_l, 3)),
                     (round(tl_hi[0] + depth * nx_l, 3), round(tl_hi[1] + depth * ny_l, 3))),
        ),
        "DOOR_E": SegmentSpec(
            "DOOR_E", "single", f"{zone_name('E')} 문", room=g.ROOM_E,
            polygon=(tr_hi,
                     (round(tr_hi[0] + depth * nx_r, 3), round(tr_hi[1] + depth * ny_r, 3)),
                     (round(br_lo[0] + depth * nx_r, 3), round(br_lo[1] + depth * ny_r, 3)),
                     br_lo),
        ),
    }

    links = [
        ("HALL_TOP", "CORRIDOR_TOP"),
        ("CORRIDOR_TOP", "CROSSROAD"),
        ("CROSSROAD", "CORRIDOR_BOTTOM"),
        ("CORRIDOR_BOTTOM", "HALL_BOTTOM"),
        ("CROSSROAD", "DOOR_B"),
        ("DOOR_B", "ROOM_B"),
        ("CROSSROAD", "DOOR_E"),
        ("DOOR_E", "ROOM_E"),
    ]
    region_to_shared = {g.TOP: "HALL_TOP", g.BOTTOM: "HALL_BOTTOM", g.ROOM_B: "ROOM_B", g.ROOM_E: "ROOM_E"}
    radius = config.resources.zone_occupied_m
    for zone in config.zones:
        parent = region_to_shared[g.ZONE_REGIONS[zone]]
        count = config.slot_count(zone)
        for slot in range(1, count + 1):
            xy = config.slot_point(zone, slot)
            name = f"{zone}{slot}"
            segments[name] = SegmentSpec(
                name=name, kind="single", label=f"{zone_name(zone)} {slot}번 자리",
                circle=(xy[0], xy[1], radius), zone=zone, slot=slot,
            )
        if count >= 2 and zone in config.exit_blocked_zones:
            links.append((f"{zone}1", f"{zone}2"))
            links.append((f"{zone}2", parent))
        else:
            for slot in range(1, count + 1):
                links.append((f"{zone}{slot}", parent))
    return segments, tuple(links)


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
    """Graph of hospital segments and shortest-path segment routing."""

    def __init__(self, config: FleetConfig):
        self.config = config
        self._top, self._ux, self._uy = _unit_corridor_axis(config)
        specs = dict(config.traffic.segments)
        links = tuple(config.traffic.links)
        if not specs or not links:
            specs, links = build_default_map_specs(config)

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
        for name in self.segments:
            self.graph.add_node(name)
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
        self.exit_map: dict[str, tuple[str, ...]] = self._build_exit_map()

    def _build_exit_map(self) -> dict[str, tuple[str, ...]]:
        """Derive each slot's exit segment purely from graph links (no zone hardcoding)."""
        exits: dict[str, tuple[str, ...]] = {}
        for name, seg in self.segments.items():
            if not seg.is_slot:
                continue
            neighbors = list(self.graph.neighbors(name))
            found = []
            for nb in neighbors:
                nb_seg = self.segments[nb]
                if nb_seg.is_slot:
                    # Slot 1 connects only through slot 2 (slot2_blocks_exit).
                    found.append(nb)
                elif nb_seg.is_shared and nb_seg.spec.room is not None:
                    # A room interior shared segment with a single non-slot exit (door).
                    non_slots = [n for n in self.graph.neighbors(nb) if not self.segments[n].is_slot]
                    if len(non_slots) == 1:
                        found.append(non_slots[0])
            exits[name] = tuple(found)
        return exits

    def projection(self, pose: g.Point) -> float:
        """Signed longitudinal metres from TOP mouth toward BOTTOM mouth."""
        return (pose[0] - self._top[0]) * self._ux + (pose[1] - self._top[1]) * self._uy

    def label(self, name: str) -> str:
        seg = self.segments.get(name)
        return seg.label if seg else name

    def nearest_slot(self, pose: g.Point) -> str | None:
        best = None
        for (_, _), name in self.slot_names.items():
            seg = self.segments[name]
            cx, cy, r = seg.spec.circle
            d = dist(pose, (cx, cy))
            if d <= r and (best is None or d < best[0]):
                best = (d, name)
        return best[1] if best else None

    def narrow_containing(self, pose: g.Point) -> list[str]:
        inc = self.config.corridor.include_boundary
        return [name for name in self.narrow_names if self.segments[name].contains(pose, include_boundary=inc)]

    def locate(self, pose: g.Point) -> str:
        """Map an arbitrary (x, y) pose to its current segment node in the graph."""
        inc = self.config.corridor.include_boundary
        region = self.config.corridor.region_of(pose)
        if region == g.CORRIDOR:
            for name in self.corridor_names:
                if self.segments[name].contains(pose, include_boundary=inc):
                    return name
            # Fallback to nearest corridor segment inside the corridor polygon.
            if self.corridor_names:
                return min(self.corridor_names, key=lambda n: self.segments[n].distance(pose))
        else:
            door = self.door_names.get(region)
            if door and self.segments[door].contains(pose, include_boundary=inc):
                return door
            for name in self.narrow_names:
                if self.segments[name].contains(pose, include_boundary=inc):
                    return name
        slot = self.nearest_slot(pose)
        if slot:
            return slot
        if region in self.region_shared:
            return self.region_shared[region]
        return min(self.segments, key=lambda n: self.segments[n].distance(pose))

    def target_node(self, destination: str, slot: int = 1, *, dock: bool = False) -> str:
        if dock:
            return self.region_shared[g.ZONE_REGIONS[destination]]
        key = (destination, slot)
        if key in self.slot_names:
            return self.slot_names[key]
        return self.slot_names[(destination, 1)]

    def path_between(self, start: str, target: str) -> tuple[str, ...]:
        return tuple(nx.shortest_path(self.graph, start, target))

    def path(self, pose: g.Point, destination: str, slot: int = 1, *, dock: bool = False) -> tuple[str, ...]:
        start = self.locate(pose)
        target = self.target_node(destination, slot, dock=dock)
        return self.path_between(start, target)

    def direction(self, seg_name: str, path: tuple[str, ...], *, pose: g.Point | None = None,
                  goal: g.Point | None = None) -> int:
        """Signed TOP -> BOTTOM (+1 / -1) direction through a one_way segment along `path`."""
        seg = self.segments[seg_name]
        if seg.kind != "one_way":
            return 0
        idx = path.index(seg_name)
        prev_pt = self.segments[path[idx - 1]].center if idx > 0 else (pose or seg.center)
        next_pt = self.segments[path[idx + 1]].center if idx + 1 < len(path) else (goal or seg.center)
        delta = self.projection(next_pt) - self.projection(prev_pt)
        return (delta > 0) - (delta < 0)

    def narrow_exit_chain(self, pose: g.Point, active_path: tuple[str, ...]) -> tuple[str, ...]:
        """Contiguous narrow segments ahead from `pose` until reaching the next shared space."""
        if not active_path:
            return ()
        current = self.locate(pose)
        if current in active_path:
            start_idx = active_path.index(current)
        else:
            # Find the closest narrow segment on active_path.
            narrow_indices = [i for i, n in enumerate(active_path) if self.segments[n].is_narrow]
            if not narrow_indices:
                return ()
            start_idx = min(narrow_indices, key=lambda i: self.segments[active_path[i]].distance(pose))
        chain = []
        for name in active_path[start_idx:]:
            if self.segments[name].is_narrow:
                chain.append(name)
            elif chain:
                break
        return tuple(chain)


@dataclass
class SegmentHolding:
    entered_at: float | None = None
    outside_since: float | None = None
    direction: int = 0
    reserved_next: bool = False

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

    def conflicting_holders(self, robot: str, direction: int = 0) -> tuple[str, ...]:
        if self.kind == "shared":
            return ()
        if self.kind == "single":
            return tuple(r for r in self.holders if r != robot)
        # one_way: same non-zero direction can share; opposite or unknown conflicts.
        return tuple(
            r for r, h in self.holders.items()
            if r != robot and (not direction or not h.direction or h.direction != direction)
        )

    def blockers(self, robot: str) -> tuple[str, ...]:
        return self.conflicting_holders(robot, 0)


@dataclass
class StopLineWait:
    leg: Leg
    segment: str
    by_robot: str
    by_level: int
    since: float
    reason: str
    dropped: tuple[str, ...]
    directions: dict[str, int]
    paused: bool = False
    exiting_cancel: bool = False


@dataclass
class _Contender:
    robot: str
    leg: Leg
    path: tuple[str, ...]
    needed: tuple[str, ...]          # non-shared segments in order to acquire
    directions: dict[str, int]
    is_reacquire: bool = False       # True for preempted active/exiting robot re-acquiring dropped tail
    exiting_cancel: bool = False
    exit_segments: frozenset[str] = frozenset()


class SegmentManager:
    """Single-rule segment reservation, preemption, stop-line pause/resume, and follow-gap safety."""

    def __init__(self, config: FleetConfig):
        self.config = config
        self.map = SegmentMap(config)
        self.robot_order = {robot: index for index, robot in enumerate(config.robots)}
        self.segments: dict[str, SegmentState] = {
            name: SegmentState(seg) for name, seg in self.map.segments.items()
        }
        # Alias for DeadlockManager / generic inspection.
        self.resources = self.segments
        self.pending: dict[str, Leg] = {}
        self.active: dict[str, Leg] = {}
        self.active_paths: dict[str, tuple[str, ...]] = {}
        self.active_dirs: dict[str, dict[str, int]] = {}
        self.exiting: dict[str, tuple[Leg, tuple[str, ...], dict[str, int]]] = {}
        self.stop_lines: dict[str, StopLineWait] = {}
        self.slot_choice: dict[Leg, int] = {}
        self.reports: dict[str, RobotReport] = {}
        self.regions: dict[str, str] = {}
        self._origins: dict[str, str] = {}
        self._finishing: list[Leg] | None = None
        self.follow: FollowStop | None = None
        self.wait_reasons: dict[str, str] = {}
        self._needed: dict[str, tuple[str, ...]] = {}
        self._decide_after = float("-inf")
        self._seg_decide_after: dict[str, float] = {}
        self.events: list[dict] = []
        self.last_decision: dict | None = None
        self.last_preempt: dict | None = None
        self.slot_support: dict[str, bool] = dict.fromkeys(config.robots, True)
        self.robot_has_job: dict[str, bool] = dict.fromkeys(config.robots, False)

    # --- Labels & Event Recording ----------------------------------------------------------------

    def robot_label(self, robot: str) -> str:
        return self.config.robots[robot].label

    def zone_label(self, zone: str, slot: int | None = None) -> str:
        name = self.config.zone_names.get(zone) or f"{zone} 구역"
        return f"{name} 2번 자리" if slot == 2 else name

    def resource_label(self, name: str) -> str:
        if name == g.CORRIDOR:
            return "복도"
        if name in self.segments:
            return self.segments[name].spec.label
        if name.startswith("ROOM_"):
            return self.zone_label(name.removeprefix("ROOM_")) + " 출입"
        return name

    def record(self, event_type, now, reason, *, robot=None, mission_id=None, candidates=None, **detail):
        if candidates is None:
            candidates = [r for r in self.pending if self.eligible(r)]
        self.events.append({
            "v": 1, "ts": now, "type": event_type, "robot": robot, "mission_id": mission_id,
            "detail": {"reason": reason, "candidates": candidates, **detail},
        })
        event = self.events[-1]
        names = detail.get("resources", ())
        if event_type in ("RESOURCE_GRANT", "RESOURCE_HOLD", "RESOURCE_PREEMPT", "CORRIDOR_FOLLOW_STOP"):
            if "rule" in event["detail"] and "state_before" in event["detail"] and "contenders" in event["detail"]:
                self.last_decision = {key: event[key] for key in ("type", "ts", "robot", "detail")}
        if event_type in ("RESOURCE_GRANT", "RESOURCE_HOLD", "RESOURCE_PREEMPT", "RESOURCE_UNKNOWN",
                          "RESOURCE_RELEASE", "RESOURCE_CLEAR_CONFIRM",
                          "CORRIDOR_FOLLOW_STOP", "CORRIDOR_FOLLOW_CLEAR"):
            for name in names:
                if name in self.segments:
                    self.segments[name].reason, self.segments[name].decided_at = reason, now

    # --- Lifecycle & External Helpers ------------------------------------------------------------

    def request(self, robot, destination, level, now, *, mission_id=None, carrying_patient=False,
                mission_type=None, dock_path=()) -> Leg:
        if robot not in self.config.robots or destination not in self.config.zones:
            raise ValueError("unknown robot or destination")
        if type(level) is not int or not 1 <= level <= 3:
            raise ValueError("level must be an integer from 1 to 3")
        if robot in self.pending or robot in self.active:
            raise ValueError(f"{robot} already has a leg")
        if not self.pending and not self.stop_lines:
            self._decide_after = max(self._decide_after, now + self.config.resources.decision_window_s)
        leg = Leg(robot, destination, level, now, mission_id, carrying_patient, mission_type, dock_path)
        self.pending[robot] = leg
        self.record("RESOURCE_REQUEST", now, f"{self.robot_label(robot)} — {self.zone_label(destination)}으로 가는 구간 요청",
                    robot=robot, mission_id=mission_id, destination=destination, level=level)
        return leg

    def begin_tick(self):
        self._finishing = []

    def finish(self, leg: Leg, now: float) -> bool:
        robot = leg.robot
        if self.pending.get(robot) is leg:
            del self.pending[robot]
        elif self.active.get(robot) is leg:
            del self.active[robot]
            self.active_paths.pop(robot, None)
            self.active_dirs.pop(robot, None)
        else:
            return False
        self._needed.pop(robot, None)
        self.wait_reasons.pop(robot, None)
        self._origins.pop(robot, None)
        self.slot_choice.pop(leg, None)
        wait = self.stop_lines.get(robot)
        if wait and wait.leg is leg:
            del self.stop_lines[robot]
        if self._finishing is None:
            self._return_reservations(leg, now)
        else:
            self._finishing.append(leg)
        return True

    def start_corridor_exit(self, robot: str, now: float, *, pose: g.Point | None = None) -> bool:
        """Called on cancel in SEGMENTS mode when robot holds an OCCUPIED narrow segment.

        Drops segments beyond the narrow exit chain immediately; keeps the contiguous narrow
        chain at Level 1 so the robot clears all narrow segments before sending CANCEL.
        """
        leg = self.active.get(robot)
        report = self.reports.get(robot)
        live_pose = pose if pose is not None else (report.pose if report else None)
        if leg is None or live_pose is None:
            return False
        if not self.holds_occupied_narrow(robot, pose=live_pose):
            return False
        path = self.active_paths.get(robot, ())
        chain = self.map.narrow_exit_chain(live_pose, path)
        if not chain:
            return False
        # Build Level-1 exiting leg preserving mission_id=None (mission already marked CANCELED).
        exit_leg = Leg(robot, leg.destination, 1, now, None, False, "CANCEL_EXIT", leg.dock_path)
        dirs = {name: self.active_dirs.get(robot, {}).get(name, 0) for name in chain}
        keep = set(chain)
        # If the next slot on the path physically overlaps the last narrow segment's exit mouth
        # (e.g. F2/D2), keep it until finish_corridor_exit so no peer claims it during coasting.
        if chain[-1] in path:
            tail = path[path.index(chain[-1]) + 1:]
            next_slot = next((n for n in tail if self.segments[n].spec.is_slot), None)
            if next_slot is not None and robot in self.segments[next_slot].holders:
                slot_seg = self.segments[next_slot].spec
                cx, cy, r = slot_seg.spec.circle
                if self.segments[chain[-1]].spec.distance((cx, cy)) < r:
                    keep.add(next_slot)
                    chain = (*chain, next_slot)
                    dirs[next_slot] = 0
        # Release any unentered segment outside the narrow exit chain immediately.
        for name, seg in self.segments.items():
            holding = seg.holders.get(robot)
            if not holding:
                continue
            if name not in keep:
                holding.reserved_next = False
                if holding.state == "GRANTED" and robot not in seg.uncertain:
                    self._release(name, robot, now, "임무 취소로 미진입 칸 예약을 반납했습니다.", leg.mission_id)
        # Remove old active leg bookkeeping without dropping the kept narrow segments.
        del self.active[robot]
        self.active_paths.pop(robot, None)
        self.active_dirs.pop(robot, None)
        self._needed.pop(robot, None)
        self.slot_choice.pop(leg, None)
        self.exiting[robot] = (exit_leg, chain, dirs)
        # If robot was preempted and has unheld segments in `chain`, retain stop-line wait on them.
        unheld = tuple(n for n in chain if robot not in self.segments[n].holders)
        old_wait = self.stop_lines.pop(robot, None)
        if unheld:
            first = unheld[0]
            by_robot = old_wait.by_robot if old_wait else next(iter(self.segments[first].holders), robot)
            by_level = old_wait.by_level if old_wait else 3
            self.stop_lines[robot] = StopLineWait(
                leg=exit_leg, segment=first, by_robot=by_robot, by_level=by_level, since=now,
                reason="취소됨 · 복도를 빠져나온 뒤 정지", dropped=unheld,
                directions={n: dirs[n] for n in unheld},
                paused=old_wait.paused if old_wait else False, exiting_cancel=True,
            )
        self.wait_reasons[robot] = "취소됨 · 복도를 빠져나온 뒤 정지"
        return True

    def finish_corridor_exit(self, robot: str, now: float):
        entry = self.exiting.pop(robot, None)
        wait = self.stop_lines.get(robot)
        if wait and wait.exiting_cancel:
            del self.stop_lines[robot]
        self.wait_reasons.pop(robot, None)
        if entry:
            exit_leg, _, _ = entry
            self._return_reservations(exit_leg, now)

    def holds_occupied_narrow(self, robot: str, *, pose: g.Point | None = None) -> bool:
        for name in self.map.narrow_names:
            holding = self.segments[name].holders.get(robot)
            if holding and holding.state == "OCCUPIED":
                return True
        report = self.reports.get(robot)
        live_pose = pose if pose is not None else (report.pose if report else None)
        if live_pose is not None:
            if self.map.narrow_containing(live_pose):
                return True
            # If actively moving toward a held narrow segment within clear_margin_m, treat as committed to exit
            # so slight pose lag or braking distance never causes coasting into an unheld narrow segment.
            if robot in self.active and report and report.departed and not report.nav_paused:
                margin = self.config.resources.clear_margin_m
                for name in self.active_paths.get(robot, ()):
                    seg = self.segments.get(name)
                    if seg and seg.spec.is_narrow and robot in seg.holders and seg.spec.distance(live_pose) <= margin:
                        return True
        return False

    def holds_any_narrow(self, robot: str) -> bool:
        return any(robot in self.segments[name].holders for name in self.map.narrow_names)

    def _return_reservations(self, leg: Leg, now: float):
        robot = leg.robot
        for name, seg in self.segments.items():
            holding = seg.holders.get(robot)
            if holding:
                holding.reserved_next = False
                if holding.state == "OCCUPIED" and seg.kind == "one_way":
                    # Keep direction only while physically still moving out, or reset when leg ends.
                    holding.direction = 0
            if holding and holding.state == "GRANTED" and robot not in seg.uncertain:
                self._release(name, robot, now, "구간이 끝나 미진입 허가를 반납했습니다.", leg.mission_id)

    def _release(self, name: str, robot: str, now: float, reason: str, mission_id=None):
        seg = self.segments[name]
        if robot not in seg.holders:
            return
        del seg.holders[robot]
        self.record("RESOURCE_RELEASE", now, reason, robot=robot, mission_id=mission_id, resources=[name])
        if seg.spec.is_narrow and seg.state == "FREE":
            self._seg_decide_after[name] = max(self._seg_decide_after.get(name, 0.0), now + self.config.resources.decision_window_s)

    def slot_of(self, leg: Leg) -> int:
        return self.slot_choice.get(leg, 1)

    def is_stop_line_paused(self, robot: str) -> bool:
        wait = self.stop_lines.get(robot)
        return bool(wait and wait.paused)

    def is_stop_line_waiting(self, robot: str) -> bool:
        return robot in self.stop_lines

    def following(self, robot: str) -> bool:
        return self.follow is not None and self.follow.rear.robot == robot

    def effective_level(self, leg: Leg, now: float) -> int:
        interval = self.config.resources.aging_s
        return leg.level + int((now - leg.since) // interval) if interval else leg.level

    def eligible(self, robot: str) -> bool:
        report = self.reports.get(robot)
        return bool(
            report and report.link_ok and report.pose is not None
            and not report.safe_pause and not report.proximity_stopped
        )

    def needed(self, robot: str) -> tuple[str, ...]:
        return self._needed.get(robot, ())

    def block_robot(self, robot: str, pose: g.Point | None, now: float) -> str | None:
        """Mark the segment(s) where a stalled/obstacle-blocked robot stands."""
        if pose is None:
            return None
        names = self.map.narrow_containing(pose) or [self.map.locate(pose)]
        for name in names:
            seg = self.segments.get(name)
            if seg is not None:
                if not seg.stall_blockers:
                    seg.blocked_since = now
                seg.stall_blockers.add(robot)
        return names[0]

    def unblock_robot(self, robot: str):
        for seg in self.segments.values():
            if robot in seg.stall_blockers:
                seg.stall_blockers.discard(robot)
                if not seg.stall_blockers:
                    seg.blocked_since = None

    def clear_unknown(self, name: str, by: str, now: float):
        if not isinstance(by, str) or not by.strip():
            raise ValueError("E-400: 조작자 이름을 입력하세요.")
        # Accept either a specific segment name or a legacy aggregate key (CORRIDOR, ROOM_B, ROOM_E).
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
            inside = self._physically_inside(seg_name)
            confirmed = seg.holders.keys() - seg.uncertain
            if seg.kind == "single" and len(confirmed.union(inside)) > 1:
                raise ValueError("E-423: 현재 보고된 로봇 수가 정원을 초과합니다.")
        for seg_name in targets:
            seg = self.segments[seg_name]
            inside = self._physically_inside(seg_name)
            reason = f"{by} — {seg.spec.label} 현장 확인 완료"
            self.record("RESOURCE_CLEAR_CONFIRM", now, reason, by=by, resources=[seg_name],
                        uncertain=sorted(seg.uncertain))
            uncertain = seg.uncertain.copy()
            seg.uncertain.clear()
            for robot in list(seg.holders):
                if robot in uncertain and robot not in inside:
                    self._release(seg_name, robot, now, reason)
            self._observe_segment(seg_name, inside, now)
            if seg.spec.is_narrow and seg.state == "FREE":
                self._seg_decide_after[seg_name] = max(self._seg_decide_after.get(seg_name, 0.0), now + self.config.resources.decision_window_s)

    # --- Physical Observation --------------------------------------------------------------------

    def _physically_inside(self, seg_name: str) -> list[str]:
        seg = self.segments[seg_name]
        if seg.spec.is_shared:
            return []
        inc = self.config.corridor.include_boundary
        found = []
        for robot, report in self.reports.items():
            if not report.link_ok or report.pose is None:
                continue
            if seg.spec.is_slot:
                ns = self.map.nearest_slot(report.pose)
                if ns != seg_name:
                    continue
                if robot not in self.active and robot not in self.exiting:
                    # Avoid double-claiming a slot already held by an online robot when passing through the hall.
                    other_online = [
                        other for other in seg.holders
                        if other != robot and other in self.reports and self.reports[other].link_ok
                    ]
                    if not other_online:
                        found.append(robot)
                else:
                    route = self.active_paths.get(robot) or (self.exiting[robot][1] if robot in self.exiting else ())
                    if seg_name in route or robot in seg.holders:
                        found.append(robot)
            else:
                if seg.spec.contains(report.pose, include_boundary=inc):
                    found.append(robot)
        return found

    def _observe_segment(self, name: str, inside: list[str], now: float):
        seg = self.segments[name]
        if seg.spec.is_shared:
            return
        policy = self.config.resources
        offline = {
            r for r in seg.holders
            if r not in self.reports or not self.reports[r].link_ok or self.reports[r].pose is None
        }
        if seg.kind == "single" and not seg.spec.is_slot and len(seg.holders.keys() | set(inside)) > 1:
            uncertain = offline | (set(inside) - seg.holders.keys())
            reason = f"{seg.spec.label}의 보고·예약 대수가 정원 1대를 초과했습니다."
        else:
            uncertain = offline
            reason = "자원 보유 로봇의 연결이 끊겼습니다."
        if uncertain - seg.uncertain:
            seg.uncertain.update(uncertain)
            robot = next(iter(uncertain)) if len(uncertain) == 1 else None
            leg = self.active.get(robot)
            self.record("RESOURCE_UNKNOWN", now, reason, robot=robot,
                        mission_id=leg.mission_id if leg else None, code="E-423", resources=[name],
                        observed=list(inside), uncertain=sorted(seg.uncertain))

        for robot in inside:
            if robot in seg.uncertain:
                continue
            if seg.kind == "single" and seg.spec.is_slot and (seg.holders.keys() - {robot}):
                continue
            holding = seg.holders.setdefault(robot, SegmentHolding())
            holding.reserved_next = False
            if holding.entered_at is None:
                holding.entered_at = now
                leg = self.active.get(robot)
                self.record("RESOURCE_ENTER", now, f"{self.robot_label(robot)} · {seg.spec.label} 진입 확인",
                            robot=robot, mission_id=leg.mission_id if leg else None, resources=[name])
            holding.outside_since = None

        for robot, holding in list(seg.holders.items()):
            if robot in seg.uncertain:
                holding.outside_since = None
                continue
            if robot in inside or holding.state == "GRANTED":
                continue
            report = self.reports[robot]
            if report.pose is None or report.safe_pause:
                holding.outside_since = None
                continue
            if seg.spec.is_slot and robot not in self.active and robot not in self.exiting:
                is_clear = self.map.nearest_slot(report.pose) != name
            else:
                is_clear = seg.spec.outside_with_margin(report.pose, policy.clear_margin_m)
            if not is_clear:
                holding.outside_since = None
                continue
            if holding.outside_since is None:
                holding.outside_since = now
            if now - holding.outside_since >= policy.clear_hold_s:
                if holding.reserved_next:
                    seg.holders[robot] = SegmentHolding(direction=holding.direction)
                    continue
                leg = self.active.get(robot)
                self._release(name, robot, now, f"{self.robot_label(robot)} · {seg.spec.label} 통과 완료",
                              leg.mission_id if leg else None)

    def _mark_passed_prefix(self, robot: str, now: float):
        """If robot already entered a later segment on its path, mark any earlier GRANTED segment as entered."""
        path = self.active_paths.get(robot) or (self.exiting[robot][1] if robot in self.exiting else ())
        if not path:
            return
        latest = -1
        for idx, name in enumerate(path):
            seg = self.segments.get(name)
            if seg and not seg.spec.is_shared:
                h = seg.holders.get(robot)
                if h and h.entered_at is not None:
                    latest = idx
        if latest <= 0:
            return
        for name in path[:latest]:
            seg = self.segments.get(name)
            if seg and not seg.spec.is_shared:
                h = seg.holders.get(robot)
                if h and h.entered_at is None:
                    h.entered_at = now

    def observe(self, now: float, reports: dict[str, RobotReport]):
        self.reports = dict(reports)
        self.regions = {
            r: self.config.corridor.region_of(s.pose)
            for r, s in reports.items() if s.link_ok and s.pose is not None
        }
        inside_map = {
            name: self._physically_inside(name)
            for name in self.segments if not self.segments[name].spec.is_shared
        }
        # First pass: record entries, then backfill any skipped earlier GRANTED segments on active paths.
        for name, inside in inside_map.items():
            seg = self.segments[name]
            for robot in inside:
                if robot not in seg.uncertain and robot in seg.holders and seg.holders[robot].entered_at is None:
                    seg.holders[robot].entered_at = now
                    seg.holders[robot].reserved_next = False
                    seg.holders[robot].outside_since = None
                    leg = self.active.get(robot)
                    self.record("RESOURCE_ENTER", now, f"{self.robot_label(robot)} · {seg.spec.label} 진입 확인",
                                robot=robot, mission_id=leg.mission_id if leg else None, resources=[name])
        for robot in (*self.active, *self.exiting):
            self._mark_passed_prefix(robot, now)
        for name, inside in inside_map.items():
            self._observe_segment(name, inside, now)
        if self._finishing is not None:
            for leg in self._finishing:
                self._return_reservations(leg, now)
            self._finishing = None

    # --- Slot Choice & Contender Building --------------------------------------------------------

    def _slot_holder(self, robot: str, zone: str, slot: int) -> str | None:
        seg_name = self.map.slot_names.get((zone, slot))
        if seg_name is not None:
            for other in self.segments[seg_name].holders:
                if other != robot:
                    return other
        for other, active_leg in self.active.items():
            if other == robot:
                continue
            if active_leg.destination == zone and (active_leg.dock_path or self.slot_of(active_leg) == slot):
                return other
        return None

    def choose_slot(self, leg: Leg) -> int:
        robot, goal = leg.robot, leg.destination
        if leg.dock_path:
            self.slot_choice.pop(leg, None)
            return 1
        holder1 = self._slot_holder(robot, goal, 1)
        slot = 1
        if holder1 and self.config.slot_count(goal) > 1 and self.slot_support.get(robot, True):
            holder2 = self._slot_holder(robot, goal, 2)
            # In a tandem exit-blocked zone, RETURN_HOME should not park IDLE in slot 2 trapping an active mission robot in slot 1.
            trap = (leg.mission_type == "RETURN_HOME" and goal in self.config.exit_blocked_zones
                    and self.robot_has_job.get(holder1, False))
            if holder2 is None and not trap:
                slot = 2
        if slot == 1:
            self.slot_choice.pop(leg, None)
        else:
            self.slot_choice[leg] = slot
        return slot

    def needs_slot_replan(self, robot: str) -> bool:
        """True if a stop-line paused robot's original destination slot became occupied by another robot while slot 2 is free."""
        wait = self.stop_lines.get(robot)
        if not wait or wait.exiting_cancel or not wait.paused or wait.leg.dock_path:
            return False
        if not self.slot_support.get(robot, True) or self.config.slot_count(wait.leg.destination) < 2:
            return False
        if self.slot_of(wait.leg) != 1:
            return False
        goal = wait.leg.destination
        s1 = self.map.slot_names.get((goal, 1))
        s2 = self.map.slot_names.get((goal, 2))
        if not s1 or not s2:
            return False
        # Replan only if slot 1 is now physically OCCUPIED by another parked robot and slot 2 is completely free.
        occ = [r for r, h in self.segments[s1].holders.items() if r != robot and h.state == "OCCUPIED" and r not in self.active]
        if wait.leg.mission_type == "RETURN_HOME" and goal in self.config.exit_blocked_zones:
            if any(self.robot_has_job.get(r, False) for r in occ):
                return False
        return bool(occ) and not self.segments[s2].holders and self._slot_holder(robot, goal, 2) is None

    def _build_contender(self, robot: str, leg: Leg, *, is_reacquire: bool = False) -> _Contender | None:
        report = self.reports.get(robot)
        if not report or not report.link_ok or report.pose is None:
            return None
        if is_reacquire:
            wait = self.stop_lines[robot]
            path = wait.dropped
            needed = tuple(n for n in wait.dropped if not self.segments[n].spec.is_shared)
            dirs = dict(wait.directions)
            return _Contender(robot=robot, leg=leg, path=path, needed=needed, directions=dirs,
                              is_reacquire=True, exiting_cancel=wait.exiting_cancel, exit_segments=frozenset())
        slot = self.choose_slot(leg)
        path = self.map.path(report.pose, leg.destination, slot, dock=bool(leg.dock_path))
        # If located near a slot held by someone else while leaving into a shared hall, don't request that peer's slot.
        effective_path = path
        if (len(path) > 1 and self.segments[path[0]].spec.is_slot
                and robot not in self.segments[path[0]].holders
                and self.segments[path[1]].spec.is_shared):
            effective_path = path[1:]
        needed = tuple(n for n in effective_path if not self.segments[n].spec.is_shared)
        goal = self.config.slot_point(leg.destination, slot) if not leg.dock_path else leg.dock_path[-1]
        dirs = {n: self.map.direction(n, path, pose=report.pose, goal=goal) for n in needed if self.segments[n].kind == "one_way"}
        start_slot = self.map.nearest_slot(report.pose)
        exits = frozenset(self.map.exit_map.get(start_slot, ())) if start_slot and start_slot != path[-1] else frozenset()
        return _Contender(robot=robot, leg=leg, path=path, needed=needed, directions=dirs,
                          is_reacquire=False, exiting_cancel=False, exit_segments=exits)

    # --- Single Comparator & Preemption Checks ---------------------------------------------------

    def _distance_to_segment(self, robot: str, seg_name: str) -> float:
        report = self.reports.get(robot)
        if report is None or report.pose is None:
            return float("inf")
        return self.segments[seg_name].spec.distance(report.pose)

    def _is_priority_one(self, contender: _Contender, seg_name: str) -> bool:
        """Priority 1: inside segment, within stop_margin_m of boundary, or exiting its zone's exit segment."""
        if seg_name in contender.exit_segments:
            return True
        return self._distance_to_segment(contender.robot, seg_name) <= self.config.traffic.stop_margin_m

    def compare_key(self, contender: _Contender, seg_name: str, now: float) -> tuple:
        return (
            0 if self._is_priority_one(contender, seg_name) else 1,
            -self.effective_level(contender.leg, now),
            contender.leg.since,
            self.robot_order[contender.robot],
        )

    def _compare_pair(self, a: _Contender, b: _Contender, shared_free: list[str], now: float) -> tuple[int, str]:
        """Compare contenders a vs b on conflicting FREE segments. Returns (-1 if a wins else 1, rule)."""
        a_p1 = any(self._is_priority_one(a, s) for s in shared_free)
        b_p1 = any(self._is_priority_one(b, s) for s in shared_free)
        if a_p1 != b_p1:
            return (-1 if a_p1 else 1), "INSIDE"
        la, lb = self.effective_level(a.leg, now), self.effective_level(b.leg, now)
        if la != lb:
            return (-1 if la > lb else 1), "LEVEL"
        if a.leg.since != b.leg.since:
            return (-1 if a.leg.since < b.leg.since else 1), "REQUEST_TIME"
        oa, ob = self.robot_order[a.robot], self.robot_order[b.robot]
        return (-1 if oa < ob else 1), "ORDER"

    def _holder_leg(self, robot: str) -> Leg | None:
        if robot in self.active:
            return self.active[robot]
        if robot in self.exiting:
            return self.exiting[robot][0]
        return None

    def _can_preempt_holder(self, requester: _Contender, holder: str, seg_name: str, now: float) -> tuple[bool, str, float | None]:
        """Verify all preemption conditions on a single conflicting (holder, seg_name)."""
        seg = self.segments[seg_name]
        holding = seg.holders.get(holder)
        if holding is None:
            return True, "PREEMPT", None
        holder_leg = self._holder_leg(holder)
        d = self._distance_to_segment(holder, seg_name)
        dist_val = round(d, 3) if d != float("inf") else None
        # Condition 1: strictly higher effective level (equal level NEVER preempts; parked robot has no active leg).
        if holder_leg is None:
            return False, "INSIDE", dist_val
        req_lvl = self.effective_level(requester.leg, now)
        hold_lvl = self.effective_level(holder_leg, now)
        if req_lvl <= hold_lvl:
            return False, "LEVEL", dist_val
        # Condition 2: holder has NOT entered the segment yet.
        if holding.entered_at is not None:
            return False, "INSIDE", dist_val
        # Never strip a destination slot out from under a holder that has already entered a narrow corridor/door
        # or has departed and will keep an earlier narrow segment (which would park it inside the corridor blocking the preemptor).
        holder_path = self.active_paths.get(holder) or (self.exiting[holder][1] if holder in self.exiting else ())
        if seg.spec.is_slot:
            if self.holds_occupied_narrow(holder):
                return False, "INSIDE", dist_val
            h_report = self.reports.get(holder)
            if h_report is not None and h_report.departed and seg_name in holder_path:
                prefix = holder_path[:holder_path.index(seg_name)]
                if any(self.segments[n].spec.is_narrow and holder in self.segments[n].holders and n not in requester.needed
                       for n in prefix):
                    return False, "INSIDE", dist_val
        # Condition 3: holder is at least stop_margin_m away from the segment boundary.
        if d < self.config.traffic.stop_margin_m or d == float("inf"):
            return False, "INSIDE", dist_val
        # Also verify holder has not entered any segment after seg_name on its path (contiguous prefix).
        if seg_name in holder_path:
            idx = holder_path.index(seg_name)
            for later in holder_path[idx + 1:]:
                lh = self.segments[later].holders.get(holder)
                if lh and lh.entered_at is not None:
                    return False, "INSIDE", dist_val
        # Physical single-lane rule: cannot preempt a leader already ahead in the SAME direction
        # on a one_way segment preceding seg_name on requester's path (requester cannot overtake).
        if seg_name in requester.path:
            req_idx = requester.path.index(seg_name)
            for earlier in requester.path[:req_idx]:
                eseg = self.segments[earlier]
                if eseg.kind != "one_way":
                    continue
                req_dir = requester.directions.get(earlier, 0)
                eh = eseg.holders.get(holder)
                if req_dir and eh and eh.direction == req_dir:
                    p_hold = self._effective_progress(holder, req_dir)
                    p_req = self._effective_progress(requester.robot, req_dir)
                    if p_hold is not None and p_req is not None and p_hold > p_req:
                        return False, "INSIDE", dist_val
        return True, "PREEMPT", dist_val

    def _doorway_obstructor(self, robot: str, seg_name: str, direction: int) -> str | None:
        """Return another robot stopped outside a parked slot center obstructing the exit ahead along `direction`."""
        seg = self.segments[seg_name]
        if seg.kind != "one_way" or not direction:
            return None
        req_report = self.reports.get(robot)
        if req_report is None or req_report.pose is None:
            return None
        req_proj = self.map.projection(req_report.pose)
        guard_m = self.config.safety.proximity_stop_m
        for other, report in self.reports.items():
            if other == robot or report.pose is None or not report.link_ok:
                continue
            if other in self.active or other in self.exiting:
                continue
            # Robots inside B/E rooms sit behind walls and never obstruct CORRIDOR_TOP/BOTTOM doorways.
            if self.regions.get(other) in g.ROOMS:
                continue
            ns = self.map.nearest_slot(report.pose)
            if ns is not None and dist(report.pose, self.segments[ns].spec.center) <= 0.15:
                continue
            # Only obstruct if `other` sits AHEAD of `robot` along `direction` (never block retreating away!).
            if direction * (self.map.projection(report.pose) - req_proj) <= 0:
                continue
            if seg.spec.distance(report.pose) < guard_m:
                # Never block `robot` if `other` is waiting for a segment `robot` currently holds (prevents circular wait).
                if other in self.pending and any(robot in self.segments[n].holders for n in self._needed.get(other, ())):
                    continue
                # Break mutual doorway obstruction symmetrically if `robot` itself is also sitting in a doorway.
                robot_in_doorway = (
                    robot not in self.active and robot not in self.exiting
                    and (self.map.nearest_slot(req_report.pose) is None
                         or dist(req_report.pose, self.segments[self.map.nearest_slot(req_report.pose)].spec.center) > 0.15)
                    and any(self.segments[n].kind == "one_way" and self.segments[n].spec.distance(req_report.pose) < guard_m
                            for n in self.map.corridor_names)
                )
                if robot_in_doorway and other in self.pending and self.robot_order[robot] < self.robot_order[other]:
                    continue
                return other
        return None

    def _evaluate(self, contender: _Contender, now: float):
        """Evaluate whether `contender` can acquire ALL its needed segments right now.

        Returns (ok, preemptions, blocker_info):
          - ok: bool
          - preemptions: dict[holder_robot, list[seg_name]] if preemption needed
          - blocker_info: None or (seg_name, blockers, rule, reason, dist_m)
        """
        robot = contender.robot
        if not self.eligible(robot):
            return False, {}, (None, (), "OFFLINE", f"{self.robot_label(robot)} — 연결·위치 또는 안전 정지 상태 확인 대기", None)

        preemptions: dict[str, list[str]] = {}
        first_blocker = None
        for name in contender.needed:
            seg = self.segments[name]
            if seg.state == "UNKNOWN":
                return False, {}, (name, tuple(sorted(seg.uncertain)), "UNKNOWN",
                                   f"{seg.spec.label} 점유 불명으로 운영자 확인을 기다립니다.",
                                   round(self._distance_to_segment(robot, name), 3))
            if seg.stall_blockers - {robot}:
                return False, {}, (name, tuple(sorted(seg.stall_blockers - {robot})), "BLOCKED",
                                   f"{seg.spec.label}에 막힘이 보고되어 대기합니다.",
                                   round(self._distance_to_segment(robot, name), 3))
            direction = contender.directions.get(name, 0)
            obstructor = self._doorway_obstructor(robot, name, direction)
            if obstructor is not None and first_blocker is None:
                d_obs = round(self._distance_to_segment(obstructor, name), 3)
                reason = f"{self.robot_label(obstructor)} {seg.spec.label} 입구 정차 중 → {self.robot_label(robot)} 대기"
                first_blocker = (name, (obstructor,), "INSIDE", reason, d_obs)
            conflicts = seg.conflicting_holders(robot, direction)
            for other in conflicts:
                # An inactive robot already physically outside a one_way segment (within clear_margin_m)
                # behind our travel direction does not block us from moving away from it.
                if (seg.kind == "one_way" and direction and other not in self.active and other not in self.exiting
                        and other in self.reports and self.reports[other].pose is not None
                        and not seg.spec.contains(self.reports[other].pose)):
                    req_pose = self.reports[robot].pose
                    if req_pose is not None and direction * (self.map.projection(self.reports[other].pose) - self.map.projection(req_pose)) < 0:
                        continue
                can, rule, d_other = self._can_preempt_holder(contender, other, name, now)
                if can:
                    preemptions.setdefault(other, []).append(name)
                elif first_blocker is None:
                    reason = f"{self.robot_label(other)} {seg.spec.label} 사용 중 → {self.robot_label(robot)} 대기"
                    first_blocker = (name, (other,), rule, reason, d_other)
        if first_blocker is not None:
            return False, {}, first_blocker
        return True, preemptions, None

    def _conflicting_free_segments(self, a: _Contender, b: _Contender) -> list[str]:
        """Segments both contenders need that are currently FREE (or unheld by either) and would conflict."""
        b_set = set(b.needed)
        shared = []
        for name in a.needed:
            if name not in b_set:
                continue
            seg = self.segments[name]
            if seg.spec.is_shared or seg.holders:
                continue
            if seg.kind == "single":
                shared.append(name)
            elif seg.kind == "one_way":
                da, db = a.directions.get(name, 0), b.directions.get(name, 0)
                if not da or not db or da != db:
                    shared.append(name)
        return shared

    @staticmethod
    def _contenders_detail(legs) -> list[dict]:
        return [
            {"robot": leg.robot, "level": leg.level, "mission_id": leg.mission_id,
             "type": leg.mission_type, "requested_at": leg.since}
            for leg in legs
        ]

    # --- Preemption Execution & Stop-Line Control ------------------------------------------------

    def _apply_preemption(self, preemptor: _Contender, preemptions: dict[str, list[str]], now: float,
                          candidates: list[str]):
        req_lvl = self.effective_level(preemptor.leg, now)
        for holder, conflict_segs in preemptions.items():
            holder_leg = self._holder_leg(holder)
            is_exiting = holder in self.exiting
            if is_exiting:
                holder_path = self.exiting[holder][1]
                holder_dirs = self.exiting[holder][2]
            else:
                holder_path = self.active_paths.get(holder, ())
                holder_dirs = self.active_dirs.get(holder, {})

            # Find the earliest preempted segment on holder's path.
            indices = [holder_path.index(s) for s in conflict_segs if s in holder_path]
            first_idx = min(indices) if indices else 0
            first_seg = holder_path[first_idx] if holder_path else conflict_segs[0]
            report = self.reports.get(holder)
            not_departed = not (report and report.departed)
            if not_departed and holder_path:
                # Holder has not departed yet: release all unentered segments so it doesn't hoard corridor/slots while stationary.
                dropped_full = tuple(
                    n for n in holder_path
                    if not self.segments[n].spec.is_shared
                    and (holder not in self.segments[n].holders or self.segments[n].holders[holder].entered_at is None)
                ) or tuple(conflict_segs)
            else:
                dropped_full = tuple(
                    n for n in holder_path[first_idx:] if not self.segments[n].spec.is_shared
                ) if holder_path else tuple(conflict_segs)
            # Also include any previously dropped tail if holder already had one (unlikely since prefix is contiguous).
            if holder in self.stop_lines:
                tail = tuple(n for n in self.stop_lines[holder].dropped if n not in dropped_full)
                dropped_full = dropped_full + tail

            dist_m = round(self._distance_to_segment(holder, first_seg), 3)
            state_before = self.segments[first_seg].state
            # Release every unentered segment in dropped_full from holder (never drop an entered segment!).
            released_names = []
            for seg_name in dropped_full:
                seg = self.segments[seg_name]
                h = seg.holders.get(holder)
                if h is not None and h.entered_at is None and holder not in seg.uncertain:
                    del seg.holders[holder]
                    released_names.append(seg_name)

            seg_label = self.segments[first_seg].spec.label
            reason = f"응급도 우선: {self.robot_label(preemptor.robot)}(Level {req_lvl})가 {seg_label} 먼저 → {self.robot_label(holder)} 정지선 대기"
            self.stop_lines[holder] = StopLineWait(
                leg=holder_leg,
                segment=first_seg,
                by_robot=preemptor.robot,
                by_level=req_lvl,
                since=now,
                reason=reason if not is_exiting else "취소됨 · 복도를 빠져나온 뒤 정지",
                dropped=dropped_full,
                directions={n: holder_dirs.get(n, 0) for n in dropped_full},
                paused=not_departed,
                exiting_cancel=is_exiting,
            )
            self.wait_reasons[holder] = self.stop_lines[holder].reason
            contenders = self._contenders_detail([preemptor.leg, *([holder_leg] if holder_leg else [])])
            self.record(
                "RESOURCE_PREEMPT", now, reason,
                robot=preemptor.robot, mission_id=preemptor.leg.mission_id, candidates=candidates,
                resources=released_names or [first_seg], segment=first_seg, preempted=holder,
                by_robot=preemptor.robot, by_level=req_lvl, distance_m=dist_m,
                rule="PREEMPT", state_before=state_before, contenders=contenders,
            )
            self.last_preempt = {
                "ts": now, "by": preemptor.robot, "by_level": req_lvl, "preempted": holder,
                "segment": first_seg, "segment_label": seg_label, "reason": reason,
            }

    def _grant_contender(self, contender: _Contender, now: float, rule: str, contenders_list: list[Leg],
                         state_before: str, dist_m: float | None, candidates: list[str],
                         ranking: list[dict], commands: list[MotionRequest]) -> Leg | None:
        robot = contender.robot
        leg = contender.leg
        for name in contender.needed:
            seg = self.segments[name]
            holding = seg.holders.setdefault(robot, SegmentHolding())
            if seg.kind == "one_way":
                holding.direction = contender.directions.get(name, 0)
            if holding.state == "OCCUPIED" and robot not in self._physically_inside(name):
                holding.reserved_next = True

        self.wait_reasons.pop(robot, None)
        self._needed.pop(robot, None)
        level = self.effective_level(leg, now)
        rule_ko = {"INSIDE": "근접·출구 우선", "LEVEL": "응급도 우선", "REQUEST_TIME": "요청 시각순",
                   "ORDER": "로봇 순서", "PREEMPT": "응급도 선점", "ONLY": "단독 요청"}.get(rule, "진입 가능")
        area = " · ".join(self.segments[n].spec.label for n in contender.needed) or "이동 구간"
        self.record(
            "RESOURCE_GRANT", now,
            f"{area} 사용 가능 · 실효 Level {level}, {rule_ko} → {self.robot_label(robot)} 허가",
            robot=robot, mission_id=leg.mission_id, candidates=candidates,
            resources=list(contender.needed), ranking=ranking,
            rule=rule, state_before=state_before, contenders=self._contenders_detail(contenders_list),
            distance_m=dist_m,
        )

        if contender.is_reacquire:
            wait = self.stop_lines.pop(robot)
            if wait.exiting_cancel:
                self.wait_reasons[robot] = "취소됨 · 복도를 빠져나온 뒤 정지"
            report = self.reports.get(robot)
            if wait.paused and report and report.departed:
                commands.append(MotionRequest(robot, "RESUME"))
            return None

        self._origins[robot] = self.regions[robot]
        self.active[robot] = self.pending.pop(robot)
        self.active_paths[robot] = contender.path
        self.active_dirs[robot] = dict(contender.directions)
        return leg

    # --- Main Arbitration Tick -------------------------------------------------------------------

    def arbitrate(self, now: float, destination_held=None) -> TickResult:
        # destination_held is ignored in SEGMENTS mode (slot segments replace zone_held).
        del destination_held
        contenders: dict[str, _Contender] = {}
        for robot, leg in list(self.pending.items()):
            c = self._build_contender(robot, leg, is_reacquire=False)
            if c is not None:
                contenders[robot] = c
                self._needed[robot] = c.needed
            else:
                self._needed[robot] = ()
        for robot, wait in list(self.stop_lines.items()):
            if robot in contenders:
                continue
            leg = self.active.get(robot) or (self.exiting[robot][0] if robot in self.exiting else None)
            if leg is None:
                del self.stop_lines[robot]
                continue
            c = self._build_contender(robot, leg, is_reacquire=True)
            if c is not None:
                contenders[robot] = c
                self._needed[robot] = c.needed

        candidates = [r for r in contenders if self.eligible(r)]
        ranking = [
            {"robot": r, "level": contenders[r].leg.level,
             "effective_level": self.effective_level(contenders[r].leg, now),
             "since": contenders[r].leg.since}
            for r in sorted(candidates, key=lambda r: (-self.effective_level(contenders[r].leg, now),
                                                     contenders[r].leg.since, self.robot_order[r]))
        ]

        grants: list[Leg] = []
        commands: list[MotionRequest] = []
        granted_robots: set[str] = set()

        # Respect decision_window_s for requests needing unheld narrow segments.
        def waiting_window(c: _Contender) -> bool:
            unheld_narrow = [n for n in c.needed if self.segments[n].spec.is_narrow and c.robot not in self.segments[n].holders]
            if not unheld_narrow:
                return False
            if now < self._decide_after:
                return True
            return any(now < self._seg_decide_after.get(n, 0.0) for n in unheld_narrow)

        # Iterative grant loop: at each step, grant the highest-priority acquirable contender.
        while True:
            acquirable: dict[str, tuple[dict[str, list[str]], str]] = {}
            for robot, c in contenders.items():
                if robot in granted_robots or waiting_window(c):
                    continue
                ok, preemptions, _ = self._evaluate(c, now)
                if ok:
                    acquirable[robot] = (preemptions, "PREEMPT" if preemptions else "ONLY")
            if not acquirable:
                break

            # Select the winner among acquirable contenders using the single comparator.
            pool = list(acquirable)
            winner = pool[0]
            winner_rule = acquirable[winner][1]
            rivals_legs = [contenders[winner].leg]
            for other in pool[1:]:
                shared = self._conflicting_free_segments(contenders[winner], contenders[other])
                if shared:
                    cmp, rule = self._compare_pair(contenders[winner], contenders[other], shared, now)
                    if cmp > 0:
                        winner = other
                        winner_rule = "PREEMPT" if acquirable[winner][0] else rule
                    elif winner_rule == "ONLY":
                        winner_rule = rule
                else:
                    # Non-conflicting: deterministic order by level/time/order.
                    kw = (-self.effective_level(contenders[winner].leg, now), contenders[winner].leg.since, self.robot_order[winner])
                    ko = (-self.effective_level(contenders[other].leg, now), contenders[other].leg.since, self.robot_order[other])
                    if ko < kw:
                        winner = other
                        winner_rule = acquirable[winner][1]
            # Collect rival legs that competed on any segment with `winner`.
            c_win = contenders[winner]
            win_set = set(c_win.needed)
            rivals_legs = [c_win.leg]
            for r_other, c_other in contenders.items():
                if r_other != winner and r_other not in granted_robots and self.eligible(r_other):
                    if win_set & set(c_other.needed):
                        rivals_legs.append(c_other.leg)
                        if winner_rule == "ONLY":
                            shared = self._conflicting_free_segments(c_win, c_other)
                            if shared:
                                _, winner_rule = self._compare_pair(c_win, c_other, shared, now)

            preemptions = acquirable[winner][0]
            contested_seg = None
            if preemptions:
                contested_seg = next((s for segs in preemptions.values() for s in segs), None)
            if contested_seg is None:
                for r_other, c_other in contenders.items():
                    if r_other != winner and r_other not in granted_robots:
                        shared = self._conflicting_free_segments(c_win, c_other)
                        if shared:
                            contested_seg = shared[0]
                            break
            if contested_seg is None:
                contested_seg = next((n for n in c_win.needed if winner not in self.segments[n].holders),
                                     c_win.needed[0] if c_win.needed else None)
            state_before = self.segments[contested_seg].state if contested_seg else "FREE"
            dist_m = round(self._distance_to_segment(winner, contested_seg), 3) if contested_seg else None

            if preemptions:
                self._apply_preemption(c_win, preemptions, now, candidates)
                winner_rule = "PREEMPT"

            granted = self._grant_contender(
                c_win, now, winner_rule, rivals_legs, state_before, dist_m, candidates, ranking, commands
            )
            if granted is not None:
                grants.append(granted)
            granted_robots.add(winner)
            # Re-evaluate slot choices for any remaining pending robot targeting the same zone.
            for r_other, leg_other in list(self.pending.items()):
                if r_other not in granted_robots and leg_other.destination == c_win.leg.destination:
                    rebuilt = self._build_contender(r_other, leg_other, is_reacquire=False)
                    if rebuilt is not None:
                        contenders[r_other] = rebuilt
                        self._needed[r_other] = rebuilt.needed

        # Record RESOURCE_HOLD for any remaining pending/ineligible robots.
        for robot, leg in list(self.pending.items()):
            if robot in granted_robots:
                continue
            c = contenders.get(robot)
            if c is None:
                reason = f"{self.robot_label(robot)} — 연결·위치 또는 안전 정지 상태 확인 대기"
                if self.wait_reasons.get(robot) != reason:
                    self.record("RESOURCE_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                                candidates=candidates, resources=[], rule="OFFLINE",
                                state_before="FREE", contenders=self._contenders_detail([leg]), distance_m=None)
                self.wait_reasons[robot] = reason
                continue
            ok, _, blocker = self._evaluate(c, now)
            if not ok and blocker is not None:
                seg_name, blockers, rule, reason, dist_m = blocker
                rivals = [leg]
                for b in blockers:
                    bl = self._holder_leg(b)
                    if bl and bl not in rivals:
                        rivals.append(bl)
                state_before = self.segments[seg_name].state if seg_name else "FREE"
                if self.wait_reasons.get(robot) != reason:
                    self.record("RESOURCE_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                                candidates=candidates, resources=[seg_name] if seg_name else list(c.needed),
                                rule=rule, state_before=state_before,
                                contenders=self._contenders_detail(rivals), distance_m=dist_m)
                self.wait_reasons[robot] = reason
            elif waiting_window(c):
                reason = "경쟁 요청을 모으는 동안 대기합니다."
                first_narrow = next((n for n in c.needed if self.segments[n].spec.is_narrow), None)
                dist_m = round(self._distance_to_segment(robot, first_narrow), 3) if first_narrow else None
                if self.wait_reasons.get(robot) != reason:
                    self.record("RESOURCE_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                                candidates=candidates, resources=list(c.needed), rule="REQUEST_TIME",
                                state_before=self.segments[first_narrow].state if first_narrow else "FREE",
                                contenders=self._contenders_detail([leg]), distance_m=dist_m)
                self.wait_reasons[robot] = reason

        # Enforce stop lines for preempted robots that are still moving toward their dropped segment.
        commands.extend(self._stop_line_commands(now))
        # Enforce same-direction follow gap safety in the corridor (ordered strictly by actual position).
        commands.extend(self._follow_commands(now))
        return TickResult(grants, commands)

    def _stop_line_commands(self, now: float) -> list[MotionRequest]:
        commands = []
        margin = self.config.traffic.stop_margin_m
        for robot, wait in list(self.stop_lines.items()):
            report = self.reports.get(robot)
            if report is None or report.pose is None:
                continue
            if not report.departed:
                wait.paused = True
                continue
            d = self._distance_to_segment(robot, wait.segment)
            if d <= margin:
                if not wait.paused:
                    wait.paused = True
                    commands.append(MotionRequest(robot, "PAUSE"))
        return commands

    # --- Same-Direction Follow Gap (Section 3.2: ordered by actual corridor-axis position) --------

    def _corridor_direction_of(self, robot: str) -> int:
        """Direction (+1 / -1) of a robot currently holding at least one one_way corridor segment."""
        dirs = {
            self.segments[n].holders[robot].direction
            for n in self.map.corridor_names
            if self.segments[n].kind == "one_way" and robot in self.segments[n].holders
               and self.segments[n].holders[robot].direction != 0
        }
        return next(iter(dirs)) if len(dirs) == 1 else 0

    def _effective_progress(self, robot: str, direction: int) -> float | None:
        """Continuous signed progress along `direction` on the corridor axis."""
        report = self.reports.get(robot)
        if report is None or report.pose is None:
            return None
        region = self.regions.get(robot)
        # A robot still inside B/E room heading into the corridor enters via the crossroad.
        if region in g.ROOMS and self.config.corridor.crossroad is not None:
            cr = self.config.corridor.crossroad
            if self.segments.get("CROSSROAD") and robot in self.segments["CROSSROAD"].holders:
                if self.segments["CROSSROAD"].holders[robot].entered_at is None:
                    return direction * self.map.projection(cr) - dist(report.pose, cr)
        return direction * self.map.projection(report.pose)

    def _corridor_gap(self, front: str, rear: str, direction: int) -> float | None:
        pf = self._effective_progress(front, direction)
        pr = self._effective_progress(rear, direction)
        if pf is None or pr is None:
            return None
        return pf - pr

    def _corridor_cleared(self, robot: str) -> bool:
        report = self.reports.get(robot)
        if report is None or report.pose is None or not report.link_ok:
            return False
        if not any(robot in self.segments[n].holders for n in self.map.corridor_names):
            return True
        # Already entered corridor and physically clear of the corridor polygon.
        entered = any(
            robot in self.segments[n].holders and self.segments[n].holders[robot].entered_at is not None
            for n in self.map.corridor_names
        )
        return entered and g.outside_with_margin(report.pose, self.config.corridor.polygon,
                                                 self.config.resources.clear_margin_m)

    def _share_one_way(self, a: str, b: str, direction: int) -> bool:
        return any(
            self.segments[n].kind == "one_way"
            and a in self.segments[n].holders and b in self.segments[n].holders
            and self.segments[n].holders[a].direction == direction
            and self.segments[n].holders[b].direction == direction
            for n in self.map.corridor_names
        )

    def _follow_commands(self, now: float) -> list[MotionRequest]:
        commands: list[MotionRequest] = []
        action = self.follow
        if action:
            commands.extend(self._update_follow(now, action))
            if self.follow is not None:
                return commands

        # Find robots holding any one_way corridor segment in the same direction.
        by_dir: dict[int, list[str]] = {}
        for robot in self.config.robots:
            d = self._corridor_direction_of(robot)
            if d and robot in self.reports and self.reports[robot].pose is not None:
                by_dir.setdefault(d, []).append(robot)

        for direction, robots in by_dir.items():
            if len(robots) < 2:
                continue
            # Order strictly by actual position along `direction` on every tick!
            ordered = sorted(robots, key=lambda r: self._effective_progress(r, direction), reverse=True)
            front, rear = ordered[0], ordered[1]
            if not self._share_one_way(front, rear, direction):
                continue
            if rear not in self.active or rear in self.stop_lines:
                continue
            if front in self.stop_lines and self.stop_lines[front].by_robot == rear:
                continue
            if self._corridor_cleared(front):
                continue
            gap = self._corridor_gap(front, rear, direction)
            if gap is None:
                continue
            close = gap <= self.config.corridor.follow_gap_m
            front_stopped = self.reports[front].stopped
            if not close and not front_stopped:
                continue
            rule = "FOLLOW" if front_stopped else "GAP"
            leg = self.active[rear]
            pos = self._effective_progress(front, direction) if rule == "FOLLOW" else None
            self.follow = FollowStop(
                resume_allowed=not self.reports[rear].stopped, front=front, rear=leg,
                direction=direction, stopped_position=pos, since=now, gap=close, rule=rule,
            )
            cause = "앞 로봇과 간격 유지" if rule == "GAP" else "앞 로봇 정지"
            reason = f"{cause} → {self.robot_label(rear)} 대기"
            self.wait_reasons[rear] = reason
            self.record(
                "CORRIDOR_FOLLOW_STOP", now, reason, robot=rear, mission_id=leg.mission_id,
                front=front, resources=[g.CORRIDOR], rule=rule, gap_m=round(gap, 3),
                state_before="OCCUPIED",
                contenders=self._contenders_detail([self.active[r] for r in (front, rear) if r in self.active]),
            )
            commands = [c for c in commands if c.robot != rear]
            commands.append(MotionRequest(rear, "PAUSE"))
            return commands
        return commands

    def _update_follow(self, now: float, action: FollowStop) -> list[MotionRequest]:
        rear, front = action.rear.robot, action.front
        same_leg = self.active.get(rear) is action.rear
        front_dir = self._corridor_direction_of(front)
        rear_dir = self._corridor_direction_of(rear)
        clear = (not same_leg or rear in self.stop_lines or front_dir != action.direction
                 or rear_dir != action.direction or not self._share_one_way(front, rear, action.direction)
                 or (front in self.stop_lines and self.stop_lines[front].by_robot == rear)
                 or self._corridor_cleared(front))
        inverted = False
        if not clear:
            report = self.reports.get(front)
            if report is None or report.pose is None or rear not in self.reports:
                return []
            gap = self._corridor_gap(front, rear, action.direction)
            if gap is None:
                return []
            # Section 3.2: if gap becomes negative, immediately re-determine front/rear!
            if gap < 0:
                inverted = True
            else:
                if gap <= self.config.corridor.follow_gap_m:
                    action.gap = True
                elif gap >= self.config.corridor.follow_resume_m:
                    action.gap = False
                if report.stopped:
                    action.stopped_position = self._effective_progress(front, action.direction)
                    return []
                pos = self._effective_progress(front, action.direction)
                if action.stopped_position is not None and pos - action.stopped_position >= self.config.stall.move_m:
                    action.stopped_position = None
                if action.gap or action.stopped_position is not None:
                    return []

        self.follow = None
        if same_leg and rear not in self.stop_lines:
            self.wait_reasons.pop(rear, None)
        reason = ("뒤 로봇의 구간 종료로 대기를 해제했습니다." if not same_leg else
                  "실제 위치 기준으로 앞뒤 순서를 다시 정해 대기를 해제했습니다." if inverted else
                  "앞 로봇이 복도를 비워 뒤 로봇 대기를 해제했습니다." if clear else
                  "앞 로봇의 전진을 확인해 뒤 로봇 대기를 해제했습니다.")
        self.record("CORRIDOR_FOLLOW_CLEAR", now, reason, robot=rear, mission_id=action.rear.mission_id,
                    front=front, resources=[g.CORRIDOR], rule=action.rule)
        follower = self.reports.get(rear)
        if same_leg and not (rear in self.stop_lines) and action.resume_allowed and self.eligible(rear) and follower.can_yield and not follower.stalled:
            return [MotionRequest(rear, "RESUME")]
        return []

    def tick(self, now: float, reports: dict[str, RobotReport]) -> TickResult:
        self.observe(now, reports)
        return self.arbitrate(now)

    # --- State Overlay & Snapshots ---------------------------------------------------------------

    def robot_state(self, robot: str, base_state: str) -> str:
        report = self.reports.get(robot)
        if base_state == "SAFE_PAUSE" or (report and report.safe_pause):
            return "SAFE_PAUSE"
        if base_state in {"OFFLINE", "FAULT"}:
            return base_state
        if self.following(robot) or self.is_stop_line_paused(robot):
            return "HOLD"
        return base_state

    def _aggregate_resource(self, seg_names: tuple[str, ...], capacity: int, *, include_follow: bool = False) -> dict:
        segs = [self.segments[n] for n in seg_names if n in self.segments]
        uncertain = any(s.state == "UNKNOWN" for s in segs)
        holders_map: dict[str, tuple[str, int]] = {}
        for s in segs:
            for r, h in s.holders.items():
                prev = holders_map.get(r)
                st = "OCCUPIED" if h.state == "OCCUPIED" or (prev and prev[0] == "OCCUPIED") else "GRANTED"
                direction = h.direction if h.direction else (prev[1] if prev else 0)
                holders_map[r] = (st, direction)
        holders = list(holders_map)[:capacity]
        if uncertain:
            state = "UNKNOWN"
        elif any(holders_map[r][0] == "OCCUPIED" for r in holders):
            state = "OCCUPIED"
        elif holders:
            state = "GRANTED"
        else:
            state = "FREE"
        blocked_robots = sorted({r for s in segs for r in s.stall_blockers})
        blocked_times = [s.blocked_since for s in segs if s.blocked_since is not None]
        decided = max(((s.decided_at, s.reason) for s in segs if s.decided_at is not None),
                      default=(None, None))
        seg_set = set(seg_names)
        waiting_legs = []
        seen_waiters = set()
        for leg in sorted(self.pending.values(), key=lambda l: (-l.level, l.since)):
            if seg_set & set(self.needed(leg.robot)):
                waiting_legs.append(leg)
                seen_waiters.add(leg.robot)
        for robot, wait in self.stop_lines.items():
            if robot not in seen_waiters and (seg_set & set(wait.dropped)):
                waiting_legs.append(wait.leg)
                seen_waiters.add(robot)
        return {
            "state": state,
            "capacity": capacity,
            "holders": holders,
            "holder": holders[0] if holders else None,
            "give_way": None,
            "holder_detail": [{"robot": r, "state": holders_map[r][0], "direction": holders_map[r][1]} for r in holders],
            "follow_stop": ({"front": self.follow.front, "rear": self.follow.rear.robot,
                             "since": self.follow.since, "rule": self.follow.rule}
                            if include_follow and self.follow else None),
            "blocked": bool(blocked_robots),
            "blocked_since": min(blocked_times) if blocked_times else None,
            "blocked_robots": blocked_robots,
            "reason": decided[1],
            "decided_at": decided[0],
            "queue_detail": [{"robot": leg.robot, "level": leg.level, "mission_id": leg.mission_id, "since": leg.since}
                             for leg in waiting_legs],
            "queue": [{"robot": leg.robot, "level": leg.level, "since": leg.since} for leg in waiting_legs],
        }

    def snapshot(self) -> dict:
        """Legacy-compatible CORRIDOR / ROOM_B / ROOM_E aggregate view."""
        door_b = (self.map.door_names[g.ROOM_B],) if g.ROOM_B in self.map.door_names else ()
        door_e = (self.map.door_names[g.ROOM_E],) if g.ROOM_E in self.map.door_names else ()
        return {
            g.CORRIDOR: self._aggregate_resource(self.map.corridor_names, 2, include_follow=True),
            g.ROOM_B: self._aggregate_resource(door_b, 1),
            g.ROOM_E: self._aggregate_resource(door_e, 1),
        }

    def traffic_snapshot(self) -> dict:
        """Detailed per-segment snapshot for /fleet/state.traffic."""
        segments_view = {}
        for name, seg in self.segments.items():
            if seg.spec.is_shared:
                continue
            dirs = {h.direction for h in seg.holders.values() if h.direction}
            segments_view[name] = {
                "kind": seg.kind,
                "label": seg.spec.label,
                "state": seg.state,
                "direction": next(iter(dirs)) if len(dirs) == 1 else 0,
                "holders": [
                    {"robot": r, "state": "UNKNOWN" if r in seg.uncertain else h.state, "direction": h.direction}
                    for r, h in seg.holders.items()
                ],
                "blocked": bool(seg.stall_blockers),
                "uncertain": sorted(seg.uncertain),
            }
        stop_waits = {}
        for robot, wait in self.stop_lines.items():
            d = self._distance_to_segment(robot, wait.segment)
            stop_waits[robot] = {
                "segment": wait.segment,
                "segment_label": self.segments[wait.segment].spec.label,
                "by": wait.by_robot,
                "by_level": wait.by_level,
                "since": wait.since,
                "paused": wait.paused,
                "exiting_cancel": wait.exiting_cancel,
                "distance_m": round(d, 3) if d != float("inf") else None,
                "reason": wait.reason,
            }
        return {
            "mode": "SEGMENTS",
            "stop_margin_m": self.config.traffic.stop_margin_m,
            "segments": segments_view,
            "stop_line_waits": stop_waits,
            "last_preempt": self.last_preempt,
        }
