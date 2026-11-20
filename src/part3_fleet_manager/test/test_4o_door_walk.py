"""Stage 4O: into and out of room B or E through the middle of the door (plan_graph.door_route).

The PLAN graph and the kinematic mock go crossroad -> door middle -> point in the room -> slot (and back out).
Every one of the 132 slot-to-slot routes keeps WALL_CLEARANCE_M from the walls of main_map/main_maps.pgm.
LEGACY keeps route_planner.waypoints, which runs 72 of them over a door frame.
"""
from math import ceil, dist, floor, hypot
from pathlib import Path

import pytest
import yaml

from kinematic_mock import KinematicRobot
from part3_fleet_manager.core import geometry as g
from part3_fleet_manager.core.plan_graph import door_route, door_walks
from part3_fleet_manager.core.plan_traffic import plan_graph
from part3_fleet_manager.core.route_planner import waypoints

MAP_YAML = Path(__file__).resolve().parents[3] / "main_map" / "main_maps.yaml"
WALL_CLEARANCE_M = 0.17
SAMPLE_M = 0.01


class WallMap:
    """Occupied cells of a ROS map (P5 PGM). Distance = from the centre of the cell a point is in to the
    nearest occupied cell centre (the costmap / distance-transform convention); 0 inside a wall cell."""

    def __init__(self, path):
        meta = yaml.safe_load(path.read_text())
        data = (path.parent / meta["image"]).read_bytes()
        fields, i = [], 0
        while len(fields) < 4:
            while data[i:i + 1].isspace():
                i += 1
            if data[i:i + 1] == b"#":
                while data[i:i + 1] not in (b"\n", b""):
                    i += 1
                continue
            j = i
            while not data[j:j + 1].isspace():
                j += 1
            fields.append(data[i:j])
            i = j
        assert fields[0] == b"P5", "binary PGM expected"
        width, height = int(fields[1]), int(fields[2])
        cells = data[i + 1:i + 1 + width * height]
        self.res = float(meta["resolution"])
        self.origin = tuple(meta["origin"][:2])
        negate, thresh = int(meta.get("negate", 0)), float(meta["occupied_thresh"])
        self.walls = set()
        for row in range(height):
            for col in range(width):
                value = cells[row * width + col]
                if (value if negate else 255 - value) / 255.0 > thresh:
                    self.walls.add((col, height - 1 - row))
        self.reach = ceil(WALL_CLEARANCE_M / self.res) + 1
        self._cell = {}

    def cell(self, p):
        return floor((p[0] - self.origin[0]) / self.res), floor((p[1] - self.origin[1]) / self.res)

    def cell_clearance(self, cell):
        found = self._cell.get(cell)
        if found is None:
            c, r = cell
            near = [hypot(dc, dr) for dc in range(-self.reach, self.reach + 1)
                    for dr in range(-self.reach, self.reach + 1) if (c + dc, r + dr) in self.walls]
            found = self._cell[cell] = min(near, default=self.reach) * self.res
        return found

    def clearance(self, line):
        """(smallest wall distance along the polyline, where)."""
        best, where = float("inf"), None
        for a, b in zip(line, line[1:]):
            n = max(1, ceil(dist(a, b) / SAMPLE_M))
            for k in range(n + 1):
                p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                d = self.cell_clearance(self.cell(p))
                if d < best:
                    best, where = d, p
        return best, where


@pytest.fixture(scope="module")
def walls():
    assert MAP_YAML.is_file(), MAP_YAML
    return WallMap(MAP_YAML)


def _slots(cfg):
    return {name: (spec.zone, spec.slot) for name, spec in cfg.traffic.segments.items()
            if spec.zone is not None and spec.slot is not None}


def _pairs(cfg):
    slots = _slots(cfg)
    return [(s, t) for s in slots for t in slots if s != t]


def _mock_line(cfg, start, target):
    zone, slot = _slots(cfg)[target]
    robot = KinematicRobot(cfg, "robot_b", start_pose=cfg.slot_point(*_slots(cfg)[start]))
    robot.receive(f"MOVE {zone}{slot} 3", 0.0)
    return [robot.pose, *robot.path]


def test_every_slot_pair_route_keeps_clear_of_the_walls(cfg, walls):
    """132 PLAN graph routes and 132 mock paths: no door frame, at least WALL_CLEARANCE_M from every wall."""
    graph = plan_graph(cfg)
    pairs = _pairs(cfg)
    assert len(pairs) == 132
    low = []
    for s, t in pairs:
        for kind, line in (("plan", graph.routes[graph.from_endpoint[s][t]].line), ("mock", _mock_line(cfg, s, t))):
            clearance, where = walls.clearance(line)
            if clearance < WALL_CLEARANCE_M:
                low.append((kind, s, t, round(clearance, 3), where))
    assert not low, low


def test_legacy_waypoints_still_cross_the_door_frames(cfg, walls):
    """The same check on route_planner.waypoints (LEGACY, unchanged) finds the 72 room routes over a frame."""
    slots = _slots(cfg)
    rooms = {name for name, (zone, _) in slots.items() if g.ZONE_REGIONS[zone] in g.ROOMS}
    low = set()
    for s, t in _pairs(cfg):
        start = cfg.slot_point(*slots[s])
        clearance, _ = walls.clearance([start, *waypoints(cfg, start, *slots[t])])
        if clearance < WALL_CLEARANCE_M:
            low.add((s, t))
    crossing = {(s, t) for s, t in _pairs(cfg) if (s in rooms or t in rooms) and slots[s][0] != slots[t][0]}
    assert len(crossing) == 72 and low == crossing


def test_door_walk_order_into_out_of_and_between_rooms(cfg):
    walks = door_walks(cfg)
    door_b, room_b = walks[g.ROOM_B]
    door_e, room_e = walks[g.ROOM_E]
    graph = plan_graph(cfg)
    assert (door_b, room_b) == (graph.points["DOOR_B"].anchor, graph.points["ROOM_B"].anchor)
    assert (door_e, room_e) == (graph.points["DOOR_E"].anchor, graph.points["ROOM_E"].anchor)
    crossroad, top, bottom = cfg.corridor.crossroad, cfg.corridor.mouths[g.TOP], cfg.corridor.mouths[g.BOTTOM]
    d1, b1, b2, c1 = (cfg.slot_point(z, s) for z, s in (("D", 1), ("B", 1), ("B", 2), ("C", 1)))
    e1 = cfg.slot_point("E", 1)
    assert door_route(cfg, d1, "B", 2) == [top, crossroad, door_b, room_b, b2]
    assert door_route(cfg, b1, "C", 1) == [room_b, door_b, crossroad, bottom, c1]
    assert door_route(cfg, b2, "E", 1) == [room_b, door_b, crossroad, door_e, room_e, e1]
    # Inside one room: slot to slot as before.
    assert door_route(cfg, b1, "B", 2) == waypoints(cfg, b1, "B", 2) == [b2]
    # Past the crossroad (PART 04 VIA_PASSED) the walk still goes through the door.
    assert door_route(cfg, crossroad, "B", 1, via_done=True) == [door_b, room_b, b1]


def test_route_made_again_mid_walk_does_not_turn_back(cfg):
    """A driving robot's route is made again from where it is: walk points it is past are left out."""
    (door_b, room_b), crossroad = door_walks(cfg)[g.ROOM_B], cfg.corridor.crossroad
    b2, d1 = cfg.slot_point("B", 2), cfg.slot_point("D", 1)

    def between(a, b, f):
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

    # Going out: between the room point and the door, then past the door middle (still in the room region).
    assert door_route(cfg, between(room_b, door_b, 0.5), "D", 1)[:2] == [door_b, crossroad]
    past_door = between(door_b, crossroad, 0.1)
    assert cfg.corridor.region_of(past_door) == g.ROOM_B
    assert door_route(cfg, past_door, "D", 1)[0] == crossroad
    # Once in the corridor nothing is added: the same mouths as route_planner.waypoints.
    in_corridor = between(door_b, crossroad, 0.5)
    assert door_route(cfg, in_corridor, "D", 1) == waypoints(cfg, in_corridor, "D", 1)
    # Coming in: still before the door middle, between the door and the room point, then past the room point.
    assert door_route(cfg, between(crossroad, door_b, 0.9), "B", 2) == [door_b, room_b, b2]
    assert door_route(cfg, between(door_b, room_b, 0.5), "B", 2) == [room_b, b2]
    assert door_route(cfg, between(room_b, b2, 0.5), "B", 2) == [b2]
    # The kinematic mock drives the same line and still reports the crossroad (VIA_PASSED).
    robot = KinematicRobot(cfg, "robot_a", start_pose=b2)
    robot.receive("MOVE D1 2", 0.0)
    assert robot.path == door_route(cfg, b2, "D", 1) and robot.via == crossroad
    assert robot.path[-1] == d1


def test_room_mode_without_doors_keeps_waypoints(cfg):
    from dataclasses import replace
    rooms = replace(cfg, corridor=replace(cfg.corridor, room_mode="ROOM"))
    assert door_walks(rooms) == {}
    start = cfg.slot_point("D", 1)
    assert door_route(rooms, start, "B", 2) == waypoints(rooms, start, "B", 2)
