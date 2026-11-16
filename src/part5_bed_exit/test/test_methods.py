"""Tests for DetectionBedExitMethod, SegmentationBedExitMethod, ROI, and tracking."""

import json
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.methods import DetectionBedExitMethod, SegmentationBedExitMethod
from part5_bed_exit.methods.common import ROI_ENV_VAR, BedROI, PatientTracker, resolve_roi


class _FakeBoxes:
    def __init__(self, xyxy, conf=None):
        self.xyxy = np.asarray(xyxy, dtype=np.float32)
        self.conf = None if conf is None else np.asarray(conf, dtype=np.float32)

    def __len__(self):
        return len(self.xyxy)


class _FakeMasks:
    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)


class _FakeResult:
    def __init__(self, boxes=None, conf=None, masks=None):
        self.boxes = None if boxes is None else _FakeBoxes(boxes, conf=conf)
        self.masks = None if masks is None else _FakeMasks(masks)


class _FakeYoloModel:
    def __init__(self, outputs):
        self._outputs = iter(outputs)
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [next(self._outputs)]


def _make_frame(h=480, w=640):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_bed_roi_validation_json_and_env_override(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        BedROI([[0.1, 0.1], [0.5, 0.5]])
    with pytest.raises(ValueError):
        BedROI([[0.0, 0.0], [1.2, 0.0], [0.5, 0.5]])
    with pytest.raises(ValueError):
        BedROI([[0.2, 0.2], [0.8, 0.2], [0.5, 0.2]])

    roi_file = tmp_path / 'custom_roi.json'
    roi_file.write_text(
        json.dumps({'bed_polygon': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]}),
        encoding='utf-8',
    )
    monkeypatch.setenv(ROI_ENV_VAR, str(roi_file))
    roi = resolve_roi()
    assert roi.box_outside_fraction(np.array([65, 49, 575, 431]), (480, 640)) == pytest.approx(0.0)
    assert roi.contains((320.0, 240.0), (480, 640)) is True
    assert roi.contains((10.0, 10.0), (480, 640)) is False


def test_patient_tracker_ignores_entering_visitor_and_resets_after_loss():
    roi = BedROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
    tracker = PatientTracker(roi, min_bed_overlap=0.2, min_track_iou=0.15, max_lost_frames=3)
    shape = (100, 100)

    idx = tracker.select(np.array([[0, 0, 15, 30], [30, 30, 70, 70]], dtype=np.float32), shape)
    assert idx == 1

    idx = tracker.select(np.array([[60, 30, 95, 70], [25, 25, 45, 45]], dtype=np.float32), shape)
    assert idx == 0

    for _ in range(3):
        with pytest.raises(RuntimeError):
            tracker.select(np.empty((0, 4), dtype=np.float32), shape)
    assert tracker.previous_box is None

    idx = tracker.select(np.array([[30, 30, 70, 70]], dtype=np.float32), shape)
    assert idx == 0


def test_detection_method_center_overlap_render_and_reset():
    roi = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    # Frame size 100x100 -> bed ROI spans x=[20..79], y=[20..79].
    model = _FakeYoloModel(
        [
            _FakeResult(boxes=[[30, 30, 70, 70]], conf=[0.9]),  # inside -> False
            _FakeResult(boxes=[[50, 30, 95, 70]], conf=[0.8]),  # outside>0.25 -> True
            _FakeResult(boxes=[[75, 30, 95, 70]], conf=[0.7]),  # center (85,50) outside -> True
            _FakeResult(boxes=[]),                              # 0 persons -> RuntimeError
        ]
    )
    detector = DetectionBedExitMethod(roi_config=roi, outside_threshold=0.25, model=model)
    frame = _make_frame(100, 100)
    frame_snapshot = frame.copy()

    res1 = detector.judge(frame)
    assert type(res1) is bool and res1 is False
    assert detector.last_result.center_inside is True
    assert detector.last_result.overlap_ratio == pytest.approx(1.0)
    rendered1 = detector.render(frame)
    assert rendered1.shape == frame.shape and not np.array_equal(rendered1, frame)

    res2 = detector.judge(frame)
    assert type(res2) is bool and res2 is True
    assert np.array_equal(frame, frame_snapshot)

    # Reset and evaluate center-outside case
    detector.reset()
    res3 = detector.judge(frame)
    assert type(res3) is bool and res3 is True
    assert detector.last_result.center_inside is False

    with pytest.raises(RuntimeError):
        detector.judge(frame)
    rendered_err = detector.render(frame)
    assert rendered_err.shape == frame.shape

    with pytest.raises(ValueError):
        detector.judge(np.zeros((10, 10), dtype=np.uint8))

    detector.close()
    with pytest.raises(RuntimeError):
        detector.judge(frame)


def test_segmentation_method_letterbox_downscale_render_and_disconnected_mask():
    roi = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    safe_mask = np.zeros((384, 480), dtype=np.float32)
    safe_mask[120:260, 150:320] = 1.0

    exit_mask = np.zeros((384, 480), dtype=np.float32)
    exit_mask[140:220, 180:280] = 1.0  # inside bed
    exit_mask[140:200, 400:440] = 1.0  # outside bed (>10%)

    empty_mask = np.zeros((384, 480), dtype=np.float32)

    model = _FakeYoloModel(
        [
            _FakeResult(boxes=[[200, 140, 420, 330]], conf=[0.85], masks=[safe_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.80], masks=[exit_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.75], masks=[empty_mask]),
        ]
    )
    detector = SegmentationBedExitMethod(
        roi_config=roi,
        outside_threshold=0.10,
        mask_max_dim=160,
        model=model,
    )
    frame = _make_frame(480, 640)
    frame_snapshot = frame.copy()

    res1 = detector.judge(frame)
    assert type(res1) is bool and res1 is False
    rendered1 = detector.render(frame)
    assert rendered1.shape == frame.shape and not np.array_equal(rendered1, frame)

    res2 = detector.judge(frame)
    assert type(res2) is bool and res2 is True
    assert detector.last_result.outside_ratio > 0.10
    assert np.array_equal(frame, frame_snapshot)
    assert model.calls[0]['retina_masks'] is False

    with pytest.raises(RuntimeError):
        detector.judge(frame)

    detector.close()


def test_zero_arg_load_via_bed_exit_method_loader(monkeypatch):
    created_calls = []

    class _StubYolo:
        def __init__(self, weights, task=None):
            created_calls.append((Path(weights).name, task))

    fake_ultralytics = types.ModuleType('ultralytics')
    fake_ultralytics.YOLO = _StubYolo
    monkeypatch.setitem(sys.modules, 'ultralytics', fake_ultralytics)

    det = load_bed_exit_method('part5_bed_exit.methods.detection_method:DetectionBedExitMethod')
    seg = load_bed_exit_method('part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod')
    assert isinstance(det, DetectionBedExitMethod)
    assert isinstance(seg, SegmentationBedExitMethod)
    assert created_calls == [('yolo11n.pt', 'detect'), ('yolo11n-seg.pt', 'segment')]
    det.close()
    seg.close()
