"""목적: ``methods/bed_exit_core.py``의 순수 판정 코드 단위를 검증한다.

대상은 ROI 기하, 환자 선택, 공통 ``judge()``, YOLO 출력 변환, Detection·
Segmentation·Pose 판정과 렌더링이다. 실제 모델 대신 NumPy 입력과 Driver Stub을 쓴다.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from part5_bed_exit.methods.bed_exit_core import (
    COCO_COMMON,
    BedExitMethod,
    Candidate,
    DetectionROI,
    Diagnostics,
    JudgmentUnavailable,
    MediaPipePoseROI,
    PatientSelector,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
    pose_decision,
)


def _roi():
    return PolygonROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])


class _Tensor:
    def __init__(self, value):
        self.value = np.asarray(value)

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class _YoloDriver:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        boxes = SimpleNamespace(
            xyxy=_Tensor([[25.0, 25.0, 75.0, 75.0]]),
            conf=_Tensor([0.9]),
        )
        return [SimpleNamespace(boxes=boxes, masks=None, keypoints=None)]


def test_polygon_roi_validates_vertices_and_calculates_box_outside_fraction():
    roi = PolygonROI([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert roi.contains((10, 10), (100, 100))
    assert not roi.contains((90, 90), (100, 100))
    assert roi.box_outside([80, 80, 95, 95], (100, 100)) == 1.0

    with pytest.raises(ValueError):
        PolygonROI([[0, 0], [1, 1], [0, 1], [1, 0]])


def test_patient_selector_selects_roi_patient_and_rejects_ambiguous_candidates():
    selector = PatientSelector(_roi())
    patient = Candidate(np.array([30, 30, 60, 70]), 0.9)
    caregiver = Candidate(np.array([85, 10, 99, 90]), 0.99)

    selected, reason = selector.select([caregiver, patient], (100, 100))
    assert selected is patient
    assert reason == 'ok'

    selector.reset()
    ambiguous = [
        Candidate(np.array([25, 25, 45, 70]), 0.9),
        Candidate(np.array([50, 25, 75, 70]), 0.8),
    ]
    assert selector.select(ambiguous, (100, 100)) == (None, 'ambiguous_people')


def test_detection_roi_judge_calls_yolo_and_returns_python_bool():
    model = _YoloDriver()
    method = DetectionROI(_roi(), 'unused.pt', model=model)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    decision = method.judge(frame)

    assert type(decision) is bool
    assert decision is False
    assert model.calls[0]['classes'] == [0]
    assert model.calls[0]['conf'] == 0.5
    assert model.calls[0]['imgsz'] == 640
    assert model.calls[0]['retina_masks'] is True


def test_segmentation_roi_decide_rejects_missing_mask_and_uses_mask_fraction():
    method = SegmentationROI(_roi(), 'unused.pt', model=object())
    candidate = Candidate(np.array([20, 20, 80, 80]), 0.9)

    assert method.decide(candidate, (100, 100)) == (None, None, 'missing_mask')

    mask = np.zeros((100, 100), dtype=np.float32)
    mask[30:70, 30:70] = 1.0
    candidate.mask = mask
    decision, score, reason = method.decide(candidate, (100, 100))
    assert decision is False
    assert score == 0.0
    assert reason == 'mask_outside_fraction'


def test_bed_exit_method_judge_reports_unavailable_for_invalid_frame_and_no_person():
    class _NoCandidateMethod(BedExitMethod):
        def candidates(self, frame):
            return []

        def decide(self, candidate, shape):
            raise AssertionError('decide must not be called')

    method = _NoCandidateMethod(_roi())

    with pytest.raises(JudgmentUnavailable, match='invalid_frame'):
        method.judge(np.zeros((10, 10), dtype=np.uint8))
    with pytest.raises(JudgmentUnavailable, match='no_person'):
        method.judge(np.zeros((100, 100, 3), dtype=np.uint8))


def test_pose_decision_requires_complete_visible_body_for_safe_result():
    points = np.tile([50.0, 50.0, 0.9], (17, 1))
    args = (_roi(), (100, 100), COCO_COMMON, (5, 6, 11, 12))

    assert pose_decision(points, *args)[0] is False
    points[9, :2] = [95, 50]
    assert pose_decision(points, *args)[0] is True
    points[9, 2] = 0.1
    assert pose_decision(points, *args)[0] is None


def test_set_roi_resets_patient_tracking():
    first = _roi()
    second = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    method = DetectionROI(first, 'unused.pt', model=object())
    method.selector.previous = np.array([10, 10, 30, 30], dtype=np.float32)

    method.set_roi(second)

    assert method.roi is second
    assert method.selector.previous is None


def test_render_methods_produce_distinct_visual_evidence():
    roi = _roi()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.float32)
    mask[20:80, 20:80] = 1.0
    detection = DetectionROI(roi, 'unused.pt', model=object())
    segmentation = SegmentationROI(roi, 'unused.pt', model=object())
    yolo_pose = YoloPoseROI(roi, 'unused.pt', model=object())
    mediapipe_pose = MediaPipePoseROI.__new__(MediaPipePoseROI)
    BedExitMethod.__init__(mediapipe_pose, roi)
    mediapipe_pose.point_conf = 0.5

    detection.last = Diagnostics(True, 'box', 0.0, (20, 20, 80, 80))
    segmentation.last = Diagnostics(True, 'mask', 0.0, (20, 20, 80, 80), mask=mask)
    yolo_points = np.tile([50.0, 50.0, 0.9], (17, 1))
    mp_points = np.tile([50.0, 50.0, 0.9], (33, 1))
    yolo_pose.last = Diagnostics(True, 'points', 0.0, points=yolo_points.tolist())
    mediapipe_pose.last = Diagnostics(True, 'points', 0.0, points=mp_points.tolist())

    rendered = [
        detection.render(frame),
        segmentation.render(frame),
        yolo_pose.render(frame),
        mediapipe_pose.render(frame),
    ]

    assert all(not np.array_equal(image, frame) for image in rendered)
    assert len({image.tobytes() for image in rendered}) == 4
