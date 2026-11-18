"""Map-frame region and clearance checks (BRIEF 5.2–5.3)."""

from collections.abc import Sequence
from math import isfinite

from shapely.geometry import LineString
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon

Point = tuple[float, float]

CORRIDOR = "CORRIDOR"
TOP = "TOP"
BOTTOM = "BOTTOM"
ROOM_B = "ROOM_B"
ROOM_E = "ROOM_E"
REGIONS = (CORRIDOR, TOP, BOTTOM, ROOM_B, ROOM_E)
ROOMS = (ROOM_B, ROOM_E)
RESOURCES = (CORRIDOR, *ROOMS)

ZONE_REGIONS = {
    "A": TOP,
    "B": ROOM_B,
    "C": BOTTOM,
    "D": TOP,
    "E": ROOM_E,
    "F": BOTTOM,
}


def _cross(a: Point, b: Point, p: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _point(p: Point) -> ShapelyPoint:
    x, y = p
    if not (isfinite(x) and isfinite(y)):
        raise ValueError("map coordinates must be finite")
    return ShapelyPoint(x, y)


def corridor_polygon(vertices: Sequence[Point]) -> Polygon:
    """Validate the strictly convex clockwise [TL, TR, BR, BL] quadrilateral."""
    if len(vertices) != 4:
        raise ValueError("expected four vertices [TL, TR, BR, BL]")
    tl, tr, br, bl = vertices
    polygon = Polygon([_point(p) for p in vertices])
    corners = (tl, tr, br, bl, tl, tr)
    if (
        not polygon.is_valid
        or not all(_cross(a, b, c) < 0 for a, b, c in zip(corners, corners[1:], corners[2:]))
        or min(tl[1], tr[1]) <= max(bl[1], br[1])
        or tl[0] >= tr[0]
        or bl[0] >= br[0]
    ):
        raise ValueError("expected a convex clockwise quadrilateral [TL, TR, BR, BL]")
    return polygon


def region_of(p: Point, corridor: Sequence[Point], *, include_boundary: bool) -> str:
    """Classify a position using the corridor edges and their extended lines."""
    polygon = corridor_polygon(corridor)
    tl, tr, br, bl = corridor
    point = _point(p)
    if polygon.contains(point):
        return CORRIDOR
    on_boundary = polygon.touches(point)
    if on_boundary and include_boundary:
        return CORRIDOR

    for a, b, region in ((tl, tr, TOP), (br, bl, BOTTOM), (bl, tl, ROOM_B)):
        if _cross(a, b, p) > 0 or (on_boundary and LineString((a, b)).covers(point)):
            return region
    return ROOM_E


def outside_with_margin(p: Point, vertices: Sequence[Point], margin: float) -> bool:
    """Check clearance beyond the buffered polygon, including rounded corners."""
    if not isfinite(margin) or margin < 0:
        raise ValueError("clearance margin must be finite and non-negative")
    point = _point(p)
    polygon = Polygon(vertices)
    if not polygon.is_valid:
        raise ValueError("invalid resource polygon")
    # The buffer approximates rounded corners with straight segments.
    return not polygon.buffer(margin).covers(point) and polygon.distance(point) > margin
