"""Unit tests for the four-method MP4 ROS benchmark wrapper."""

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
