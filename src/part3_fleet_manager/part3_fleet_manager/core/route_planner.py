"""Zone routes and physical clearance in the map frame (BRIEF 5.2)."""

import networkx as nx

from . import geometry as g
from .config import FleetConfig


def waypoints(config, pose, destination):
    goal = config.zones[destination]
    start, end = (config.corridor.region_of(p) for p in (pose, goal))
    points = []
    if start != end:
        if start != g.CORRIDOR:
            points.append(config.corridor.mouths[start])
        points.append(config.corridor.mouths[end])
    return [*points, goal]


class RoutePlanner:
    def __init__(self, config: FleetConfig):
        self.corridor = config.corridor
        self.zones = config.zones
        self.graph = nx.Graph()
        self.graph.add_edges_from((g.CORRIDOR, side) for side in (g.TOP, g.BOTTOM, *g.ROOMS))

    def required(self, pose: g.Point, destination: str) -> tuple[str, ...]:
        start = self.corridor.region_of(pose)
        end = self.corridor.region_of(self.zones[destination])
        path = nx.shortest_path(self.graph, start, end)
        return tuple(region for region in path if region in g.RESOURCES)

    def is_clear(self, resource: str, pose: g.Point, margin: float) -> bool:
        vertices = self.corridor.polygon
        if resource in g.ROOMS:
            vertices = self._room_polygon(resource, pose, margin)
        elif resource != g.CORRIDOR:
            raise ValueError(f"unknown resource {resource}")
        return g.outside_with_margin(pose, vertices, margin)

    def _room_polygon(self, room: str, pose: g.Point, margin: float):
        """Extend the two hall boundaries past the query disk, without a map-size cutoff."""
        tl, tr, br, bl = self.corridor.polygon
        if room == g.ROOM_B:
            top, bottom = tl, bl
            outer_x = min(tl[0], bl[0], pose[0] - margin) - (tr[0] - tl[0])
        else:
            top, bottom = tr, br
            outer_x = max(tr[0], br[0], pose[0] + margin) + (tr[0] - tl[0])
        top_slope = (tr[1] - tl[1]) / (tr[0] - tl[0])
        bottom_slope = (br[1] - bl[1]) / (br[0] - bl[0])
        upper = top[1] + top_slope * (outer_x - top[0])
        lower = bottom[1] + bottom_slope * (outer_x - bottom[0])
        if upper <= lower:
            # Converging hall boundaries meet before the outer cut.
            x = outer_x + (lower - upper) / (top_slope - bottom_slope)
            return (top, bottom, (x, top[1] + top_slope * (x - top[0])))
        return (top, bottom, (outer_x, lower), (outer_x, upper))
