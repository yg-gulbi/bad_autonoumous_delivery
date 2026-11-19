"""목적: 64e57ce 운영 segmentation 모듈의 MP4→판정→5프레임 투표 연결 검증.

계획: 1,024프레임 전체를 현재 create_method()와 BedExitState에 순서대로 입력한다.
입력: test_input_sequence1.mp4, methods_v1.json, yolo11n-seg.pt.
예상 출력: 1,024프레임 완주, 유효 Bool 존재, True·False 모두 존재, 예외는 미발행.
실제 출력: PASS, 유효 990, 판정 불가 34, True 605, False 385, EOF 발행 0.
"""

import os
from pathlib import Path

import cv2
import pytest

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable
from part5_bed_exit.pretrained import create_method


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
VIDEO_PATH = PACKAGE_ROOT / 'research/data/videos/test_input_sequence1.mp4'
CONFIG_PATH = PACKAGE_ROOT / 'research/configs/test_input_sequence1_methods_v1.json'


@pytest.mark.integration
def test_mp4_uses_current_segmentation_and_five_frame_vote():
    """1,024프레임 전체를 현재 판단 객체와 투표 상태에 통과시킨다."""
    if os.environ.get('PART5_RUN_MP4_INTEGRATION') != '1':
        pytest.skip('Set PART5_RUN_MP4_INTEGRATION=1 for actual model inference')

    pytest.importorskip('ultralytics')
    assert VIDEO_PATH.is_file()
    assert CONFIG_PATH.is_file()

    capture = cv2.VideoCapture(str(VIDEO_PATH))
    assert capture.isOpened()
    detector = create_method(CONFIG_PATH)
    state = BedExitState()
    total_frames = 0
    unavailable_frames = 0
    published_true = 0
    published_false = 0

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
            if unsafe:
                published_true += 1
            else:
                published_false += 1
    finally:
        capture.release()
        detector.close()

    published = published_true + published_false
    print({
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
