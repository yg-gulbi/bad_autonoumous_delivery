import pytest

from part3_fleet_manager.core import geometry


@pytest.mark.parametrize(("zone", "region"), [
    ("A", geometry.TOP), ("B", geometry.ROOM_B), ("C", geometry.BOTTOM),
    ("D", geometry.TOP), ("E", geometry.ROOM_E), ("F", geometry.BOTTOM),
])
def test_zone_goals(cfg, zone, region):
    assert cfg.corridor.region_of(cfg.zones[zone]) == region


@pytest.mark.parametrize(("point", "region"), [
    ((-1.60, -2.00), geometry.CORRIDOR),
    ((-2.10, -0.45), geometry.TOP),
    ((-2.10, -0.52), geometry.ROOM_B),
    ((-0.50, -4.35), geometry.BOTTOM),
    ((-0.50, -4.26), geometry.ROOM_E),
    ((-2.00, -3.80), geometry.CORRIDOR),
    ((-2.00, -1.20), geometry.ROOM_B),
])
def test_regions_follow_tilted_edges(cfg, point, region):
    assert cfg.corridor.region_of(point) == region


@pytest.mark.parametrize(("point", "adjoining_region"), [
    ((-2, 0), geometry.TOP), ((-2, -4), geometry.BOTTOM),
    ((-3, -2), geometry.ROOM_B), ((-1, -2), geometry.ROOM_E),
    ((-3, 0), geometry.TOP),
])
def test_corridor_boundary_policy(point, adjoining_region):
    rectangle = [(-3, 0), (-1, 0), (-1, -4), (-3, -4)]
    assert geometry.region_of(point, rectangle, include_boundary=True) == geometry.CORRIDOR
    assert geometry.region_of(point, rectangle, include_boundary=False) == adjoining_region


@pytest.mark.parametrize(("point", "is_clear"), [
    ((-1.60, -2.00), False),
    ((-0.99, -2.40), False),
    ((-0.55, -2.40), True),
])
def test_corridor_margin(cfg, point, is_clear):
    assert geometry.outside_with_margin(
        point, cfg.corridor.polygon, cfg.resources.clear_margin_m
    ) is is_clear


def test_buffer_boundary_is_still_occupied():
    square = [(0, 1), (1, 1), (1, 0), (0, 0)]
    assert not geometry.outside_with_margin((1.25, 0.5), square, 0.25)
    assert not geometry.outside_with_margin((1.15, 1.15), square, 0.25)
    assert geometry.outside_with_margin((1.20, 1.20), square, 0.25)


@pytest.mark.parametrize("point", [(float("nan"), 0), (0, float("inf"))])
def test_invalid_coordinates_have_no_region(cfg, point):
    with pytest.raises(ValueError):
        cfg.corridor.region_of(point)
