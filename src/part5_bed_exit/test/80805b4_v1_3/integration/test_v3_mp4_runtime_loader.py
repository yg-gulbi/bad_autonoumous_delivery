"""실제 MP4를 런타임 로더로 만든 Segmentation 판단 객체에 연결한다."""

import os
from pathlib import Path

import cv2
import pytest

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
VIDEO_PATH = PACKAGE_ROOT / 'research/data/videos/test_input_sequence1.mp4'
CONFIG_PATH = PACKAGE_ROOT / 'research/configs/test_input_sequence1_methods_v1.json'


@pytest.mark.integration
def test_mp4_uses_runtime_loader_segmentation_and_five_frame_vote():
    if os.environ.get('PART5_RUN_V3_MP4_INTEGRATION') != '1':
        pytest.skip('Set PART5_RUN_V3_MP4_INTEGRATION=1 for actual model inference')

    pytest.importorskip('ultralytics')
    assert VIDEO_PATH.is_file()
    assert CONFIG_PATH.is_file()

    capture = cv2.VideoCapture(str(VIDEO_PATH))
    assert capture.isOpened()
    detector = load_bed_exit_method('segmentation', str(CONFIG_PATH))
    state = BedExitState()
    total_frames = unavailable_frames = published_true = published_false = 0

    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            total_frames += 1
            try:
                unsafe = state.update(detector.judge(frame))
            except JudgmentUnavailable:
                unavailable_frames += 1
                state = BedExitState()
                continue
            published_true += int(unsafe)
            published_false += int(not unsafe)
    finally:
        capture.release()
        detector.close()

    published = published_true + published_false
    print({
        'loader': 'load_bed_exit_method',
        'total_frames': total_frames,
        'unavailable_frames': unavailable_frames,
        'published': published,
        'published_true': published_true,
        'published_false': published_false,
    })
    assert total_frames == 1024
    assert published > 0
    assert published + unavailable_frames == total_frames
    assert published_true > 0
    assert published_false > 0
