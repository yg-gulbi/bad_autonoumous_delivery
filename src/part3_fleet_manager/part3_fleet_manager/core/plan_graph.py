"""Stage 4O PLAN point graph: points, the physical route pieces between them, travel steps and conflicts.

Points are the zone slots, the robot_b dock, the crossroad, the B/E doors and rooms, the two hall entrances
and the corridor cells (CORRIDOR_TOP/BOTTOM split into about ``cell_m``). Every point keeps the region of
its segment in the segment map for the "inside" judgement.

Robots never get a path from us: PART 04 drives toward a slot. The graph walks ``door_route``: the
``waypoints`` mouths and crossroad, and into or out of room B or E through the middle of the door and the
point inside the room (``door_walks``), so no route runs over a door frame. Each run of a route inside one
point's region is a *piece*: the
exact line the robot drives there. Neighbouring points are consecutive pieces, travel time is the piece
length over ``plan_speed_mps``, and two positions conflict when their pieces come closer than the
proximity distance (moving robots) or the slot distance (a robot standing at a slot). Nothing about a
particular zone pair or situation is written here; it all follows from geometry.
"""

import copy
from dataclasses import dataclass
from itertools import pairwise
from math import ceil, dist, hypot

from . import geometry as g
from .clearance import stop_distance

SAMPLE_M = 0.02          # walk step along a route when assigning points
PRUNE_OVER = 2000        # routes made for robot positions kept before old ones are forgotten (long runs)
MIN_RUN_M = 0.03         # shorter runs at a region edge are folded into the neighbour (boundary jitter)


def _cross(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _seg_point_dist(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _seg_seg_dist(a, b, c, d):
    def orient(p, q, r):
        v = _cross(p, q, r)
        return (v > 1e-12) - (v < -1e-12)
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return 0.0
    return min(_seg_point_dist(a, c, d), _seg_point_dist(b, c, d), _seg_point_dist(c, a, b), _seg_point_dist(d, a, b))


def polyline_distance(pa, pb):
    """Minimum distance between two polylines (a single point is a polyline of one vertex)."""
    if len(pa) == 1 and len(pb) == 1:
        return dist(pa[0], pb[0])
    if len(pa) == 1:
        return min(_seg_point_dist(pa[0], a, b) for a, b in pairwise(pb))
    if len(pb) == 1:
        return min(_seg_point_dist(pb[0], a, b) for a, b in pairwise(pa))
    return min(_seg_seg_dist(a, b, c, d) for a, b in pairwise(pa) for c, d in pairwise(pb))


def _length(points):
    return sum(dist(a, b) for a, b in pairwise(points))


def _sub_polyline(points, s0, s1):
    """The part of a polyline between arc lengths s0 and s1."""
    out = [point_at(points, s0)]
    s = 0.0
    for a, b in pairwise(points):
        s += dist(a, b)
        if s0 + 1e-9 < s < s1 - 1e-9 and dist(out[-1], b) > 1e-9:
            out.append(b)
    end = point_at(points, s1)
    if dist(out[-1], end) > 1e-9:
        out.append(end)
    return tuple(out)


def project(points, pose, lo=0.0, hi=float("inf")):
    """Arc length of the closest point of ``points`` to ``pose`` within [lo, hi]."""
    best, best_s, s = float("inf"), lo, 0.0
    for a, b in pairwise(points):
        seg = dist(a, b)
        if seg > 1e-12 and s + seg >= lo and s <= hi:
            t = ((pose[0] - a[0]) * (b[0] - a[0]) + (pose[1] - a[1]) * (b[1] - a[1])) / (seg * seg)
            t = max((lo - s) / seg, 0.0, min(t, 1.0, (hi - s) / seg))
            q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            d = dist(q, pose)
            if d < best - 1e-12:
                best, best_s = d, s + t * seg
        s += seg
    return best_s


def point_at(points, s_target):
    s = 0.0
    for a, b in pairwise(points):
        seg = dist(a, b)
        if s + seg >= s_target - 1e-12 and seg > 1e-12:
            t = max(0.0, min(1.0, (s_target - s) / seg))
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        s += seg
    return points[-1]


class Circle:
    def __init__(self, cx, cy, r):
        self.c, self.r = (cx, cy), r

    def contains(self, p):
        return dist(p, self.c) <= self.r

    def distance(self, p):
        return max(0.0, dist(p, self.c) - self.r)


class Poly:
    def __init__(self, points):
        self.points = tuple(points)

    def contains(self, p):
        x, y, inside = p[0], p[1], False
        for (x1, y1), (x2, y2) in pairwise((*self.points, self.points[0])):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def distance(self, p):
        if self.contains(p):
            return 0.0
        return min(_seg_point_dist(p, a, b) for a, b in pairwise((*self.points, self.points[0])))


class Region:
    """A shared region (hall or room) of the corridor half-plane partition (geometry.region_of)."""

    def __init__(self, corridor, region):
        self.corridor, self.region = corridor, region

    def contains(self, p):
        return region_of(p, self.corridor) == self.region

    def distance(self, p):
        return 0.0 if self.contains(p) else 1.0


def region_of(p, corridor):
    """Fast pure-Python copy of geometry.region_of (boundary goes to the corridor)."""
    tl, tr, br, bl = corridor
    if _cross(tl, tr, p) <= 0 and _cross(tr, br, p) <= 0 and _cross(br, bl, p) <= 0 and _cross(bl, tl, p) <= 0:
        return g.CORRIDOR
    for a, b, region in ((tl, tr, g.TOP), (br, bl, g.BOTTOM), (bl, tl, g.ROOM_B)):
        if _cross(a, b, p) > 0:
            return region
    return g.ROOM_E


def point_anchor(spec, seg):
    """Where a graph point (``traffic.graph.points``) stands: the centre of its segment's circle, its own
    x, y, or the middle of its segment's polygon."""
    if seg.circle is not None:
        return tuple(seg.circle[:2])
    if spec.anchor is not None:
        return tuple(spec.anchor)
    if seg.polygon:
        return tuple(sum(c) / len(seg.polygon) for c in zip(*seg.polygon))
    return None


def waypoints(config, pose, destination, slot=1, *, via_done=False):
    """Mouths between regions; travel into or out of room B·E stops at the crossroad first (PART 04)."""
    goal = config.slot_point(destination, slot)
    start, end = (config.corridor.region_of(p) for p in (pose, goal))
    points = []
    if start != end and (start in g.ROOMS or end in g.ROOMS):
        if start in (g.TOP, g.BOTTOM):
            points.append(config.corridor.mouths[start])
        if not via_done:
            points.append(config.corridor.crossroad)
        if end in (g.TOP, g.BOTTOM):
            points.append(config.corridor.mouths[end])
    elif start != end:
        if start != g.CORRIDOR:
            points.append(config.corridor.mouths[start])
        points.append(config.corridor.mouths[end])
    return [*points, goal]


def door_walks(config):
    """{room region: (door middle, point inside the room)} from the graph's door and room points."""
    segs = config.traffic.segments
    doors, rooms = {}, {}
    for spec in config.traffic.graph.points.values():
        seg = segs.get(spec.segment) if spec.segment else None
        if seg is None:
            continue
        if spec.kind == "door" and seg.room in g.ROOMS:
            doors[seg.room] = point_anchor(spec, seg)
        elif spec.kind == "room" and seg.region in g.ROOMS:
            rooms[seg.region] = point_anchor(spec, seg)
    return {room: (doors[room], rooms[room]) for room in g.ROOMS
            if doors.get(room) is not None and rooms.get(room) is not None}


def door_route(config, pose, destination, slot=1, *, via_done=False):
    """``waypoints`` with the walk through a B·E door: crossroad -> door middle -> point in
    the room -> slot, and the same way back out (B -> E: room B point, door B, crossroad, door E, room E
    point). A route made again from where a driving robot is now leaves out the walk points it is already
    past: a robot is past a point once it is nearer the next point than that point is. A MOVE to a slot of
    the room the robot is in also goes out to the crossroad and back (PART 04 does so); only after the
    crossroad (``via_done``) does a robot inside a room go straight to the slot."""
    path = list(waypoints(config, pose, destination, slot, via_done=via_done))
    walks = door_walks(config)
    if not walks:
        return path
    goal = path[-1]
    start, end = config.corridor.region_of(pose), config.corridor.region_of(goal)
    if start == end:
        if end not in walks:
            return path
        door, room = walks[end]
        if not via_done:
            # PART 04 drives every MOVE to room B or E to the crossroad first (``dest in VIA_ROOMS``), also from
            # inside that room: out through the door, the crossroad, and back in (11/21 12:43:41).
            out = [room, door] if dist(pose, door) >= dist(room, door) else [door]
            return [*out, config.corridor.crossroad, door, room, goal]
        if dist(pose, door) >= dist(room, door):
            return path                     # standing in the room: slot to slot
        return [room, goal] if dist(pose, room) < dist(door, room) else [door, room, goal]
    out = list(path[:-1])
    if end in walks:
        out += walks[end]
    if start in walks:
        door, room = walks[start]
        after = out[0] if out else goal
        if dist(pose, door) >= dist(room, door):
            out = [room, door, *out]
        elif dist(pose, after) >= dist(door, after):
            out = [door, *out]
    return [*out, goal]


@dataclass(frozen=True)
class Point:
    name: str
    kind: str          # slot | dock | crossroad | door | room | hall | cell
    label: str
    anchor: g.Point
    region: object
    segment: str | None = None   # segment-map name shown on the screen (cells keep their corridor segment)
    zone: str | None = None
    slot: int | None = None


@dataclass(frozen=True)
class Piece:
    id: int
    point: str
    line: tuple            # polyline the robot drives inside the point (or one vertex when standing)
    length: float
    steps: int
    standing: bool = False  # a robot stopped here (end of a route)
    at_slot: bool = False   # standing at a zone slot (proximity uses slot_stop_m)
    extra: tuple = ()       # segments still held on the board at the plan start (not yet released)
    fence: bool = False     # an UNKNOWN segment nobody may use (no line of its own for the proximity rule)


@dataclass(frozen=True)
class Route:
    id: int
    start: str | None      # endpoint name, or None for a route from a free pose
    target: str            # endpoint name (slot or dock) the MOVE is sent to
    pieces: tuple          # piece ids in driving order
    starts: tuple          # arc length where each piece starts
    line: tuple            # full polyline
    length: float
    park: int              # standing piece at the target


class PlanGraph:
    """Built once per configuration; ad-hoc routes from a robot's current pose are added on demand."""

    def __init__(self, config):
        self.config = config
        traffic = config.traffic
        gcfg = traffic.graph
        self.speed = gcfg.plan_speed_mps or config.mock.speed_mps
        self.step_s = gcfg.plan_step_s
        self.step_m = self.speed * self.step_s
        self.start_steps = ceil(gcfg.move_start_s / self.step_s - 1e-9)
        self.safety = config.safety
        self.corridor = tuple(config.corridor.polygon)
        segs = traffic.segments
        self.points: dict[str, Point] = {}
        for name, spec in segs.items():
            if spec.zone is not None and spec.slot is not None:
                cx, cy, r = spec.circle
                self.points[name] = Point(name, "slot", spec.label, (cx, cy), Circle(cx, cy, r), name, spec.zone, spec.slot)
        self.region_points = {}
        for name, spec in gcfg.points.items():
            if spec.kind == "dock":
                dock = config.robots[spec.robot].dock
                if dock is None:
                    continue
                xy = (dock.x, dock.y)
                self.points[name] = Point(name, "dock", spec.label, xy,
                                          Circle(xy[0], xy[1], config.resources.zone_occupied_m), None,
                                          zone=self._nearest_zone(xy))
                continue
            seg = segs[spec.segment]
            if seg.circle is not None:
                region = Circle(*seg.circle)
            elif seg.polygon:
                region = Poly(seg.polygon)
            else:
                region = Region(self.corridor, seg.region)
                self.region_points[seg.region] = name
            self.points[name] = Point(name, spec.kind, spec.label, point_anchor(spec, seg), region, spec.segment)
        self.cells = []
        for seg_name in gcfg.corridor:
            self._split_corridor(seg_name, segs[seg_name], gcfg.cell_m)
        self.doors = [n for n, p in self.points.items() if p.kind == "door"]
        # Segment capacity from the segment map: a single segment holds one robot, a one_way segment
        # holds robots going the same way. Slots count only for the robot that stops there.
        self.capacity = {}
        for name, spec in segs.items():
            if spec.kind in ("single", "one_way") and spec.zone is None:
                region = Circle(*spec.circle) if spec.circle is not None else Poly(spec.polygon)
                self.capacity[name] = (spec.kind, region)
        top, bottom = config.corridor.mouths[g.TOP], config.corridor.mouths[g.BOTTOM]
        length = dist(top, bottom)
        self._axis = (top, ((bottom[0] - top[0]) / length, (bottom[1] - top[1]) / length))
        self._segs: dict = {}
        self.clear_margin = config.resources.clear_margin_m
        self.crossroads = [n for n, p in self.points.items() if p.kind == "crossroad"]
        self.endpoints = {n: p for n, p in self.points.items() if p.kind in ("slot", "dock")}
        self.pieces: dict[int, Piece] = {}
        self._piece_keys: dict = {}
        self._next_piece = 0
        self.routes: dict[int, Route] = {}
        self._route_keys: dict = {}
        self._next_route = 0
        self._dist: dict = {}
        self._conflict: dict = {}
        self._suffix: dict = {}
        self.park = {name: self._piece(name, (p.anchor,), standing=True, at_slot=p.kind == "slot")
                     for name, p in self.endpoints.items()}
        self.from_endpoint: dict[str, dict[str, int]] = {n: {} for n in self.endpoints}
        for s in self.endpoints:
            for t in self.endpoints:
                if s != t and self._drivable(s, t):
                    self.from_endpoint[s][t] = self.route_from(self.endpoints[s].anchor, t, start=s)
        self.edges = set()
        for route in self.routes.values():
            names = [self.pieces[p].point for p in route.pieces]
            self.edges.update((a, b) for a, b in pairwise(names) if a != b)
        self._fixed = (self._next_piece, self._next_route)   # the map's own pieces and routes, never pruned

    def fork(self):
        """A copy that adds and forgets its own routes (one per running traffic manager)."""
        new = copy.copy(self)
        for name in ("pieces", "_piece_keys", "routes", "_route_keys", "_dist", "_conflict", "_suffix", "_segs"):
            setattr(new, name, dict(getattr(self, name)))
        return new

    def prune(self, keep_routes, keep_pieces=(), *, over=PRUNE_OVER):
        """Forget routes and pieces made for earlier robot positions once there are more than ``over`` of
        them, except those in use. Returns True when something was forgotten."""
        fixed_pieces, fixed_routes = self._fixed
        if len(self.routes) - fixed_routes <= over:
            return False
        keep_r = {r for r in keep_routes if r in self.routes}
        keep_p = set(keep_pieces)
        for r in keep_r:
            keep_p.update(self.routes[r].pieces)
            keep_p.add(self.routes[r].park)
        self.routes = {r: v for r, v in self.routes.items() if r < fixed_routes or r in keep_r}
        self.pieces = {p: v for p, v in self.pieces.items() if p < fixed_pieces or p in keep_p}
        self._route_keys = {k: v for k, v in self._route_keys.items() if v in self.routes}
        self._piece_keys = {k: v for k, v in self._piece_keys.items() if v in self.pieces}
        self._suffix = {k: v for k, v in self._suffix.items() if k in self.routes}
        self._segs = {k: v for k, v in self._segs.items() if k in self.pieces}
        self._dist = {k: v for k, v in self._dist.items() if k[0] in self.pieces and k[1] in self.pieces}
        self._conflict = {k: v for k, v in self._conflict.items() if k[0] in self.pieces and k[1] in self.pieces}
        return True

    # --- construction ---------------------------------------------------------------------------

    def _nearest_zone(self, xy):
        region = region_of(xy, self.corridor)
        zones = [z for z, r in g.ZONE_REGIONS.items() if r == region] or list(self.config.zones)
        return min(zones, key=lambda z: dist(self.config.zones[z], xy))

    def _split_corridor(self, seg_name, spec, cell_m):
        v0, v1, v2, v3 = spec.polygon
        length = (dist(v0, v3) + dist(v1, v2)) / 2
        count = max(1, round(length / cell_m))
        for k in range(count):
            a, b = k / count, (k + 1) / count
            left = (v0[0] + (v3[0] - v0[0]) * a, v0[1] + (v3[1] - v0[1]) * a)
            left2 = (v0[0] + (v3[0] - v0[0]) * b, v0[1] + (v3[1] - v0[1]) * b)
            right = (v1[0] + (v2[0] - v1[0]) * a, v1[1] + (v2[1] - v1[1]) * a)
            right2 = (v1[0] + (v2[0] - v1[0]) * b, v1[1] + (v2[1] - v1[1]) * b)
            poly = (left, right, right2, left2)
            name = f"{seg_name}_{k + 1}"
            anchor = tuple(sum(c) / 4 for c in zip(*poly))
            self.points[name] = Point(name, "cell", f"{spec.label} {k + 1}칸", anchor, Poly(poly), seg_name)
            self.cells.append(name)

    def _drivable(self, s, t):
        dock_s, dock_t = self.endpoints[s].kind == "dock", self.endpoints[t].kind == "dock"
        return not (dock_s and dock_t)

    def _route_points(self, start_xy, target, via_done=False):
        point = self.endpoints[target]
        if point.kind == "dock":
            dock = next(r.dock for r in self.config.robots.values() if r.dock and (r.dock.x, r.dock.y) == point.anchor)
            approach, contact = dock.path_end()
            path = door_route(self.config, start_xy, point.zone, 1, via_done=via_done)[:-1]
            return (start_xy, *path, approach, contact)
        return (start_xy, *door_route(self.config, start_xy, point.zone, point.slot, via_done=via_done))

    def _classify(self, xy, own):
        for name in own:
            if self.points[name].region.contains(xy):
                return name
        for name in self.doors:
            if self.points[name].region.contains(xy):
                return name
        for name in self.crossroads:
            if self.points[name].region.contains(xy):
                return name
        for name in self.cells:
            if self.points[name].region.contains(xy):
                return name
        region = region_of(xy, self.corridor)
        if region == g.CORRIDOR:
            return min(self.cells, key=lambda n: self.points[n].region.distance(xy))
        return self.region_points[region]

    def _piece(self, point, line, *, standing=False, at_slot=False, extra=(), fence=False):
        rounded = tuple((round(x, 3), round(y, 3)) for x, y in line)
        extra = tuple(sorted(extra))
        key = (point, rounded, standing, at_slot, extra, fence)
        found = self._piece_keys.get(key)
        if found is not None:
            return found
        length = _length(line)
        steps = 1 if standing else max(1, round(length / self.step_m))
        piece = Piece(self._next_piece, point, tuple(line), length, steps, standing, at_slot, extra, fence)
        self._next_piece += 1
        self.pieces[piece.id] = piece
        self._piece_keys[key] = piece.id
        return piece.id

    def route_from(self, start_xy, target, *, start=None, via_done=False, extra=(), cut=None):
        """Route id from a position to an endpoint, cut into pieces by point regions. ``extra`` segments
        are added to the first piece (still held on the board where the robot stands now). ``cut`` also
        cuts the piece at that distance along the route (the stop line of a driving robot), so the part it
        cannot avoid is a piece of its own."""
        start_xy = (float(start_xy[0]), float(start_xy[1]))
        extra = tuple(sorted(extra))
        key = (round(start_xy[0], 3), round(start_xy[1], 3), target, start, via_done, extra,
               None if cut is None else round(cut, 3))
        found = self._route_keys.get(key)
        if found is not None:
            return found
        line = self._route_points(start_xy, target, via_done)
        own = [target] + ([start] if start else [])
        total = _length(line)
        samples = max(2, ceil(total / SAMPLE_M) + 1)
        labels = [self._classify(point_at(line, total * i / (samples - 1)), own) for i in range(samples)]
        runs = []   # [point, s_start, s_end]
        for i, name in enumerate(labels):
            s = total * i / (samples - 1)
            if runs and runs[-1][0] == name:
                runs[-1][2] = s
            else:
                if runs:
                    mid = (runs[-1][2] + s) / 2
                    runs[-1][2] = mid
                    runs.append([name, mid, s])
                else:
                    runs.append([name, 0.0, s])
        merged = []
        for run in runs:
            if merged and (run[2] - run[1] < MIN_RUN_M or merged[-1][0] == run[0]):
                merged[-1][2] = run[2]
            else:
                merged.append(run)
        if len(merged) > 1 and merged[0][2] - merged[0][1] < MIN_RUN_M:
            merged[1][1] = 0.0
            merged.pop(0)
        merged[-1][2] = total
        if cut is not None:
            for i, (name, s0, s1) in enumerate(merged):
                if s0 + MIN_RUN_M <= cut <= s1 - MIN_RUN_M:
                    merged[i:i + 1] = [[name, s0, cut], [name, cut, s1]]
                    break
        pieces, starts = [], []
        for name, s0, s1 in merged:
            if s1 - s0 <= 1e-9 and pieces:
                continue
            pieces.append(self._piece(name, _sub_polyline(line, s0, s1), extra=() if pieces else extra))
            starts.append(s0)
        route = Route(self._next_route, start, target, tuple(pieces), tuple(starts), line, total, self.park[target])
        self._next_route += 1
        self.routes[route.id] = route
        self._route_keys[key] = route.id
        return route.id

    def stand(self, xy, extra=(), *, slot_rule=True):
        """Standing piece at a free pose (stopped on the way). It does not occupy a slot, but inside a slot
        circle the proximity rule treats it as standing at a slot (collision.parked_slot). ``slot_rule``
        False: the proximity rule keeps proximity_stop_m for this robot (it has a granted leg)."""
        at_slot = slot_rule and any(p.kind == "slot" and p.region.contains(xy) for p in self.endpoints.values())
        return self._piece(self._classify(xy, ()), ((round(xy[0], 3), round(xy[1], 3)),), standing=True,
                           at_slot=at_slot, extra=extra)

    def parked(self, endpoint, extra=(), *, slot_rule=True):
        """Standing piece at an endpoint, with segments it still holds (same place as ``park``)."""
        point = self.endpoints[endpoint]
        at_slot = slot_rule and point.kind == "slot"
        if not extra and at_slot == (point.kind == "slot"):
            return self.park[endpoint]
        return self._piece(endpoint, (point.anchor,), standing=True, at_slot=at_slot, extra=extra)

    def fence(self, segment):
        """A position that takes a whole segment and nothing else: an UNKNOWN segment on the board, which no
        robot may use until the operator has checked it (it is not where any robot stands, D-54)."""
        spec = self.config.traffic.segments[segment]
        if spec.circle is not None:
            anchor = tuple(spec.circle[:2])
        elif spec.polygon:
            anchor = tuple(sum(c) / len(spec.polygon) for c in zip(*spec.polygon))
        else:
            anchor = (0.0, 0.0)
        return self._piece("FENCE:" + segment, (anchor,), standing=True, extra=((segment, 0),), fence=True)

    def stay_route(self, endpoint=None, *, piece=None):
        """A route with no pieces: standing at an endpoint (its park) or at a free standing piece."""
        park = self.park[endpoint] if piece is None else piece
        key = ("stay", endpoint, park)
        found = self._route_keys.get(key)
        if found is not None:
            return found
        line = self.pieces[park].line
        route = Route(self._next_route, endpoint, endpoint, (), (), line, 0.0, park)
        self._next_route += 1
        self.routes[route.id] = route
        self._route_keys[key] = route.id
        return route.id

    # --- queries --------------------------------------------------------------------------------

    def distance(self, a, b):
        key = (a, b) if a < b else (b, a)
        found = self._dist.get(key)
        if found is None:
            found = self._dist[key] = polyline_distance(self.pieces[a].line, self.pieces[b].line)
        return found

    def threshold(self, a, b):
        """Proximity threshold between two positions (clearance.stop_distance, the proximity stop's own rule);
        None when both stand still (no stop can occur)."""
        pa, pb = self.pieces[a], self.pieces[b]
        if (pa.standing and pb.standing) or pa.fence or pb.fence:
            return None
        return stop_distance(self.safety, pa.standing and pa.at_slot, pb.standing and pb.at_slot)

    def segs(self, piece_id):
        """{segment: direction} the position uses (single, one_way, or its own slot)."""
        found = self._segs.get(piece_id)
        if found is not None:
            return found
        piece = self.pieces[piece_id]
        if piece.fence:
            self._segs[piece_id] = out = dict(piece.extra)
            return out
        out = {}
        point = self.points.get(piece.point)
        if point is not None and point.kind == "slot":
            out[point.segment] = 0
        line = piece.line
        samples = [line[0]] if len(line) == 1 else [
            point_at(line, piece.length * (i + 0.5) / n) for n in (max(2, ceil(piece.length / SAMPLE_M)),) for i in range(n)]
        (tx, ty), (ux, uy) = self._axis
        delta = ((line[-1][0] - line[0][0]) * ux + (line[-1][1] - line[0][1]) * uy) if len(line) > 1 else 0.0
        direction = 0 if piece.standing or abs(delta) < 0.02 else (1 if delta > 0 else -1)
        for name, (kind, region) in self.capacity.items():
            if any(region.contains(xy) for xy in samples):
                out[name] = direction if kind == "one_way" else 0
        for name, direction in piece.extra:
            out.setdefault(name, direction)
        self._segs[piece_id] = out
        return out

    def shares(self, a, b):
        """Segments a and b cannot use together (single, or one_way in opposite or unknown directions)."""
        sa, sb = self.segs(a), self.segs(b)
        out = []
        for name in sa.keys() & sb.keys():
            kind = self.capacity[name][0] if name in self.capacity else "single"
            if kind == "single" or not sa[name] or not sb[name] or sa[name] != sb[name]:
                out.append(name)
        return out

    def conflict(self, a, b):
        key = (a, b) if a < b else (b, a)
        found = self._conflict.get(key)
        if found is None:
            limit = self.threshold(a, b)
            found = (limit is not None and self.distance(a, b) < limit) or bool(self.shares(a, b))
            self._conflict[key] = found
        return found

    def route_steps(self, route_id, index=0, remaining=None):
        """Steps from piece ``index`` (``remaining`` steps left in it, None = all) to the route end."""
        suffix = self._suffix.get(route_id)
        if suffix is None:
            steps = [self.pieces[p].steps for p in self.routes[route_id].pieces]
            suffix = [0] * (len(steps) + 1)
            for i in range(len(steps) - 1, -1, -1):
                suffix[i] = suffix[i + 1] + steps[i]
            self._suffix[route_id] = suffix = tuple(suffix)
        if index >= len(suffix) - 1:
            return 0
        if remaining is None:
            return suffix[index]
        return suffix[index + 1] + remaining

    def label(self, name):
        point = self.points.get(name)
        return point.label if point else name
