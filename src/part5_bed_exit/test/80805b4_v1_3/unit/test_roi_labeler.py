import json

import pytest

from part5_bed_exit.roi_labeler import (
    build_method_config,
    normalize_points,
    save_config,
)


def test_normalize_points_uses_frame_edge_as_one():
    assert normalize_points(
        [(0, 0), (639, 0), (639, 479), (0, 479)], 640, 480
    ) == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


def test_normalize_points_rejects_crossed_click_order():
    with pytest.raises(ValueError, match='boundary order'):
        normalize_points([(0, 0), (9, 9), (9, 0), (0, 9)], 10, 10)


def test_build_and_save_method_config(tmp_path):
    output = tmp_path / 'camera_0_methods.json'
    config = build_method_config(
        [(10, 10), (90, 10), (90, 90), (10, 90)],
        (101, 101, 3),
        output,
        camera_index=0,
    )
    saved = save_config(output, config)
    loaded = json.loads(saved.read_text(encoding='utf-8'))

    assert loaded['method'] == 'segmentation'
    assert loaded['roi'] == [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    assert loaded['roi_metadata']['camera_index'] == 0
    assert loaded['roi_metadata']['frame_width'] == 101

    with pytest.raises(FileExistsError, match='--force'):
        save_config(output, config)
