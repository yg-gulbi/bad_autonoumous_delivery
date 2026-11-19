"""통합 테스트 보조 검증: 4모델 MP4 벤치마크의 결합 규칙을 확인한다.

대상 코드는 ``four_method_mp4_benchmark.py``의 Pose 추적 손실 변환과 모델별 ROS
토픽 이름 생성이다. 실제 MP4·모델·ROS 통신을 실행하는 전체 통합시험이 아니라,
통합 실행기가 사용하는 경계 규칙을 가짜 detector로 빠르게 검증한다.
"""

import numpy as np
import pytest

from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable
from research.integration.four_method_mp4_benchmark import (
    PoseLossAfterTrack, topic_for,
)


class _Detector:
    def __init__(self, results):
        self.results = iter(results)

    def judge(self, frame):
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_pose_loss_becomes_exit_only_after_successful_track():
    frame = np.zeros((8, 8, 3), np.uint8)
    detector = PoseLossAfterTrack(_Detector([
        JudgmentUnavailable('no_person'),
        False,
        JudgmentUnavailable('missing_keypoints'),
        JudgmentUnavailable('ambiguous_people'),
    ]))
    with pytest.raises(JudgmentUnavailable, match='no_person'):
        detector.judge(frame)
    assert detector.judge(frame) is False
    assert detector.judge(frame) is True
    with pytest.raises(JudgmentUnavailable, match='ambiguous_people'):
        detector.judge(frame)


def test_four_method_topics_are_distinct():
    methods = ('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose')
    topics = [topic_for('/patient/bed_exit', name) for name in methods]
    assert len(set(topics)) == 4
    assert topics[-1] == '/patient/bed_exit/mediapipe_pose'
