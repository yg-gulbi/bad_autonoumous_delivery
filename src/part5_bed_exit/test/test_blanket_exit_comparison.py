import numpy as np

from research.poc.run_blanket_exit_comparison import (
    add_textured_blanket,
    composite_person,
    derive_ensemble_predictions,
    tile_grid,
)


def test_composite_person_translates_mask_and_pixels():
    frame = np.full((120, 80, 3), 30, dtype=np.uint8)
    frame[30:90, 25:55] = (20, 80, 210)
    mask = np.zeros((120, 80), dtype=np.uint8)
    mask[30:90, 25:55] = 1

    composite, moved = composite_person(frame, mask, dx=12, dy=8)

    assert composite.shape == frame.shape
    assert moved.shape == mask.shape
    assert moved.dtype == np.bool_
    assert moved[38:98, 37:67].all()
    assert not moved[30:38, 25:37].any()


def test_textured_blanket_is_nonempty_and_changes_frame():
    frame = np.full((120, 80, 3), 100, dtype=np.uint8)
    person = np.zeros((120, 80), dtype=bool)
    person[20:105, 22:58] = True
    texture = np.full((40, 30, 3), (180, 100, 40), dtype=np.uint8)

    covered, blanket = add_textured_blanket(frame, person, texture)

    assert covered.shape == frame.shape
    assert blanket.shape == frame.shape[:2]
    assert blanket.any()
    assert np.any(covered != frame)


def test_tile_grid_pads_last_row():
    tiles = [np.full((10, 8, 3), value, dtype=np.uint8) for value in (1, 2, 3)]
    grid = tile_grid(tiles, columns=2)

    assert grid.shape == (20, 16, 3)
    assert np.all(grid[:10, :8] == 1)
    assert np.all(grid[:10, 8:] == 2)
    assert np.all(grid[10:, :8] == 3)
    assert np.all(grid[10:, 8:] == 0)


def test_ensemble_uses_pose_positive_and_requires_safe_agreement():
    predictions = {
        "yolo_segment_sensitive": False,
        "yolo_pose_any": True,
        "mediapipe_pose_any": None,
    }
    derived = derive_ensemble_predictions(predictions)
    assert derived["ensemble_pose_positive_else_segment"][0] is True
    assert derived["ensemble_conservative_positive"][0] is True

    predictions.update({"yolo_pose_any": None, "mediapipe_pose_any": None})
    derived = derive_ensemble_predictions(predictions)
    assert derived["ensemble_pose_positive_else_segment"][0] is False
    assert derived["ensemble_conservative_positive"][0] is None

    predictions["yolo_pose_any"] = False
    derived = derive_ensemble_predictions(predictions)
    assert derived["ensemble_conservative_positive"][0] is False
