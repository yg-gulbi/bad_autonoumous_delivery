import pytest

from part3_fleet_manager.core.route_planner import RoutePlanner


@pytest.mark.parametrize(("start", "end", "needed"), [
    ("A", "D", ()), ("C", "F", ()), ("A", "F", ("CORRIDOR",)),
    ("D", "B", ("CORRIDOR", "ROOM_B")), ("E", "F", ("ROOM_E", "CORRIDOR")),
    ("B", "E", ("ROOM_B", "CORRIDOR", "ROOM_E")), ("B", "B", ("ROOM_B",)),
])
def test_graph_route_in_both_directions(cfg, start, end, needed):
    planner = RoutePlanner(cfg)
    assert planner.required(cfg.zones[start], end) == needed
    assert planner.required(cfg.zones[end], start) == needed[::-1]


def test_start_in_corridor_and_on_configured_boundary(cfg):
    planner = RoutePlanner(cfg)
    assert planner.required((-1.6, -2.0), "E") == ("CORRIDOR", "ROOM_E")
    assert planner.required(cfg.corridor.polygon[0], "F") == ("CORRIDOR",)


@pytest.mark.parametrize(("room", "near_door", "inside", "far_hall"), [
    ("ROOM_B", (-1.94, -2.1), "B", (-5, 1)),
    ("ROOM_E", (-1.09, -2.25), "E", (5, -6)),
])
def test_room_clearance_uses_extended_hall_edges(cfg, room, near_door, inside, far_hall):
    planner = RoutePlanner(cfg)
    margin = cfg.resources.clear_margin_m
    assert not planner.is_clear(room, near_door, margin)
    assert not planner.is_clear(room, cfg.zones[inside], margin)
    assert planner.is_clear(room, (-1.6, -2), margin)
    assert planner.is_clear(room, far_hall, margin)
