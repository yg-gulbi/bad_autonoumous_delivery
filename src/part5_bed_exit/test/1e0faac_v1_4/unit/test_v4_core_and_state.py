"""목적: v1.4의 ``bed_exit_core.py``·``bed_exit_state.py`` 파라미터화 단위를 검증한다."""

from types import SimpleNamespace

import numpy as np
import pytest

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import (
    Candidate,
    DetectionROI,
    PatientSelector,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
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


def test_patient_selector_respects_custom_thresholds_and_validates():
    roi = _roi()
    edge_candidate = Candidate(np.array([0.0, 20.0, 30.0, 80.0]), 0.9)
    default_selector = PatientSelector(roi)
    assert default_selector.select([edge_candidate], (100, 100)) == (
        None, 'patient_unassociated'
    )

    relaxed_selector = PatientSelector(roi, init_overlap=0.25, min_iou=0.05, ambiguity_gap=0.05)
    selected, reason = relaxed_selector.select([edge_candidate], (100, 100))
    assert selected is edge_candidate
    assert reason == 'ok'

    shifted = Candidate(np.array([25.0, 20.0, 60.0, 80.0]), 0.9)
    selected2, reason2 = relaxed_selector.select([shifted], (100, 100))
    assert selected2 is shifted
    assert reason2 == 'ok'

    relaxed_selector.reset()
    c1 = Candidate(np.array([25.0, 25.0, 75.0, 75.0]), 0.9)
    c2 = Candidate(np.array([15.0, 25.0, 75.0, 75.0]), 0.8)
    assert default_selector.select([c1, c2], (100, 100)) == (None, 'ambiguous_people')
    assert relaxed_selector.select([c1, c2], (100, 100)) == (c1, 'ok')

    for bad_kwargs in (
        {'min_iou': -0.1},
        {'min_iou': 1.1},
        {'init_overlap': -0.1},
        {'ambiguity_gap': 1.5},
    ):
        with pytest.raises(ValueError):
            PatientSelector(roi, **bad_kwargs)


def test_yolo_and_segmentation_respect_custom_imgsz_mask_threshold_and_set_roi():
    model = _YoloDriver()
    method = DetectionROI(
        _roi(),
        'unused.pt',
        confidence=0.35,
        imgsz=320,
        outside_fraction=0.1,
        min_iou=0.25,
        init_overlap=0.4,
        ambiguity_gap=0.1,
        model=model,
    )
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert method.judge(frame) is False
    assert model.calls[0]['conf'] == 0.35
    assert model.calls[0]['imgsz'] == 320

    method.set_roi(PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]))
    assert method.selector.min_iou == 0.25
    assert method.selector.init_overlap == 0.4
    assert method.selector.ambiguity_gap == 0.1

    seg = SegmentationROI(_roi(), 'unused.pt', mask_threshold=0.8, outside_fraction=0.05, model=object())
    mask = np.zeros((100, 100), dtype=np.float32)
    mask[30:70, 30:70] = 0.9
    mask[0:20, 0:20] = 0.7
    candidate = Candidate(np.array([0.0, 0.0, 70.0, 70.0]), 0.9, mask=mask)
    decision_high, score_high, _ = seg.decide(candidate, (100, 100))
    assert decision_high is False
    assert score_high == 0.0

    seg_default = SegmentationROI(_roi(), 'unused.pt', model=object())
    decision_def, score_def, _ = seg_default.decide(candidate, (100, 100))
    assert decision_def is True
    assert score_def > 0.05

    with pytest.raises(ValueError, match='imgsz'):
        DetectionROI(_roi(), 'unused.pt', imgsz=0, model=object())
    with pytest.raises(ValueError, match='imgsz'):
        DetectionROI(_roi(), 'unused.pt', imgsz=True, model=object())
    with pytest.raises(ValueError, match='mask_threshold'):
        SegmentationROI(_roi(), 'unused.pt', mask_threshold=1.2, model=object())
    with pytest.raises(ValueError, match='confidence'):
        DetectionROI(_roi(), 'unused.pt', confidence=-0.1, model=object())


def test_yolo_pose_validates_policy_point_conf_and_tolerance():
    pose = YoloPoseROI(
        _roi(), 'unused.pt', policy='torso', point_conf=0.3, tolerance_px=5.0, model=object()
    )
    assert pose.policy == 'torso'
    assert pose.point_conf == 0.3
    assert pose.tolerance_px == 5.0

    with pytest.raises(ValueError, match='policy'):
        YoloPoseROI(_roi(), 'unused.pt', policy='invalid', model=object())
    with pytest.raises(ValueError, match='point_conf'):
        YoloPoseROI(_roi(), 'unused.pt', point_conf=1.5, model=object())
    with pytest.raises(ValueError, match='tolerance_px'):
        YoloPoseROI(_roi(), 'unused.pt', tolerance_px=-1.0, model=object())


def test_custom_window_and_threshold_vote():
    state = BedExitState(window_size=3, threshold=2)

    assert [state.update(v) for v in (True, False, True, False, False)] == [
        False, False, True, False, False,
    ]
    assert state.window_size == 3
    assert state.threshold == 2


def test_prefill_safe_eliminates_warmup_delay_and_preserves_on_reset():
    state = BedExitState(window_size=5, threshold=3, prefill_safe=True)
    assert list(state.history) == [False, False, False, False, False]

    assert [state.update(True) for _ in range(3)] == [False, False, True]
    assert state.unsafe is True

    state.reset()
    assert list(state.history) == [False, False, False, False, False]
    assert state.unsafe is False


def test_reset_preserves_custom_config_without_prefill():
    state = BedExitState(window_size=4, threshold=2, prefill_safe=False)
    state.update(True)
    state.update(True)
    state.reset()

    assert list(state.history) == []
    assert state.history.maxlen == 4
    assert state.threshold == 2
    assert state.unsafe is False


@pytest.mark.parametrize(
    'kwargs,exc_type',
    [
        ({'window_size': 0}, ValueError),
        ({'window_size': -1}, ValueError),
        ({'window_size': True}, ValueError),
        ({'window_size': 5, 'threshold': 0}, ValueError),
        ({'window_size': 5, 'threshold': 6}, ValueError),
        ({'window_size': 5, 'threshold': True}, ValueError),
        ({'prefill_safe': 1}, TypeError),
    ],
)
def test_invalid_state_parameters_raise(kwargs, exc_type):
    with pytest.raises(exc_type):
        BedExitState(**kwargs)
