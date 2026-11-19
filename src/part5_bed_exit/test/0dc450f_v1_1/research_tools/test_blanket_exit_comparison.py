"""연구/POC: 담요 환경 비교 실험기의 영상 합성·배치 함수를 검증한다.

``research/poc/run_blanket_exit_comparison.py``로 담요 가림 조건에서 여러 모델을
비교하기 위해 만든 보조 테스트다. 사람 마스크 이동, 담요 질감 합성, 결과 타일 배치와
앙상블 규칙을 작은 배열로 확인한다. 최종 운영 단위·통합시험에는 포함하지 않는다.
"""

import numpy as np

from research.poc.run_blanket_exit_comparison import (
    add_textured_blanket,
    composite_person,
    derive_ensemble_predictions,
    tile_grid,
)


def test_composite_person_translates_mask_and_pixels():
    """사람 합성이 마스크와 대응 픽셀을 같은 거리만큼 이동하는지 검증한다."""
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
    """담요 질감 합성이 비어 있지 않은 마스크와 변경 영상을 만드는지 검증한다."""
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
    """타일 수가 열 수로 나누어지지 않아도 마지막 행을 채우는지 검증한다."""
    tiles = [np.full((10, 8, 3), value, dtype=np.uint8) for value in (1, 2, 3)]
    grid = tile_grid(tiles, columns=2)

    assert grid.shape == (20, 16, 3)
    assert np.all(grid[:10, :8] == 1)
    assert np.all(grid[:10, 8:] == 2)
    assert np.all(grid[10:, :8] == 3)
    assert np.all(grid[10:, 8:] == 0)


def test_ensemble_uses_pose_positive_and_requires_safe_agreement():
    """앙상블이 Pose 양성을 우선하고 정상에는 합의를 요구하는지 검증한다."""
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
