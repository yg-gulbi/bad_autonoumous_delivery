"""Tests for BedROI, PatientTracker, SegmentationBedExitMethod, GPU resolution, and 3-of-5 voting."""

import json
from pathlib import Path
import sys
import types

import numpy as np
import pytest

import compare_methods as cm
from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods import SegmentationBedExitMethod
from part5_bed_exit.methods.common import (
    DEVICE_ENV_VAR,
    ROI_ENV_VAR,
    BedROI,
    PatientTracker,
    default_camera_index,
    resolve_device,
    resolve_roi,
)


class _FakeBoxes:
    # The production code reads Ultralytics results through these two attributes.
    def __init__(self, xyxy, conf=None):
        self.xyxy = np.asarray(xyxy, dtype=np.float32)
        self.conf = None if conf is None else np.asarray(conf, dtype=np.float32)

    def __len__(self):
        return len(self.xyxy)


class _FakeMasks:
    # Keep mask data in the same array-like shape as Ultralytics' result object.
    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)


class _FakeResult:
    # None represents the missing boxes/masks cases handled by the detector.
    def __init__(self, boxes=None, conf=None, masks=None):
        self.boxes = None if boxes is None else _FakeBoxes(boxes, conf=conf)
        self.masks = None if masks is None else _FakeMasks(masks)


class _FakeYoloModel:
    # Returning one queued result per call makes the test deterministic across frames.
    def __init__(self, outputs):
        self._outputs = iter(outputs)
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [next(self._outputs)]


def _make_frame(h=480, w=640):
    # A zero-valued, valid BGR frame is sufficient because inference is mocked.
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_bed_roi_validation_json_and_env_override(tmp_path, monkeypatch):
    # Reject malformed polygons before checking configuration-file resolution.
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
    # The configured polygon covers the box, so no pixels should be outside it.
    assert roi.box_outside_fraction(np.array([65, 49, 575, 431]), (480, 640)) == pytest.approx(0.0)


def test_resolve_device_prefers_cuda_when_available(monkeypatch):
    # Replace torch only for this test so device selection is independent of host hardware.
    fake_torch = types.ModuleType('torch')
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    monkeypatch.delenv(DEVICE_ENV_VAR, raising=False)

    assert resolve_device('auto') == 'cuda:0'
    assert resolve_device(None) == 'cuda:0'
    assert resolve_device('cpu') == 'cpu'

    monkeypatch.setenv(DEVICE_ENV_VAR, 'cuda:1')
    assert resolve_device('auto') == 'cuda:1'


def test_patient_tracker_ignores_entering_visitor_and_resets_after_loss():
    roi = BedROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
    tracker = PatientTracker(roi, min_bed_overlap=0.2, min_track_iou=0.15, max_lost_frames=3)
    shape = (100, 100)

    # Initial acquisition uses ROI overlap, not detection order, to select the patient.
    idx = tracker.select(np.array([[0, 0, 15, 30], [30, 30, 70, 70]], dtype=np.float32), shape)
    assert idx == 1

    # Once acquired, IoU preserves the identity even when the patient leaves the ROI.
    idx = tracker.select(np.array([[60, 30, 95, 70], [25, 25, 45, 45]], dtype=np.float32), shape)
    assert idx == 0

    # Temporary detector misses raise an error but retain tracking until the loss limit.
    for _ in range(3):
        with pytest.raises(RuntimeError):
            tracker.select(np.empty((0, 4), dtype=np.float32), shape)
    assert tracker.previous_box is None

    # After the lock is reset, a later in-bed detection can be acquired again.
    idx = tracker.select(np.array([[30, 30, 70, 70]], dtype=np.float32), shape)
    assert idx == 0


def test_patient_tracker_handles_rapid_shift_bed_reacquire_and_far_loss():
    roi = BedROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
    tracker = PatientTracker(
        roi,
        min_bed_overlap=0.2,
        min_track_iou=0.15,
        max_lost_frames=3,
        max_center_jump=0.25,
    )
    shape = (100, 100)

    # Start locked on patient inside bed ROI.
    assert tracker.select(np.array([[20, 40, 45, 60]], dtype=np.float32), shape) == 0

    # Zero-IoU rapid posture shift within max_center_jump stays locked on the same person.
    idx = tracker.select(
        np.array([[70, 60, 80, 80], [46, 30, 56, 65]], dtype=np.float32),
        shape,
    )
    assert idx == 1

    # Walk outside the ROI in two steps while center proximity holds the lock.
    assert tracker.select(np.array([[68, 25, 86, 45]], dtype=np.float32), shape) == 0
    assert tracker.select(np.array([[85, 5, 99, 25]], dtype=np.float32), shape) == 0

    # Disappearing far off-bed when an occupant is in the bed ROI re-acquires immediately.
    assert tracker.select(np.array([[25, 25, 65, 75]], dtype=np.float32), shape) == 0

    # A distant person far outside the bed ROI must still count as lost and reset tracking.
    for _ in range(3):
        with pytest.raises(RuntimeError, match='Tracked patient lost'):
            tracker.select(np.array([[0, 0, 12, 15]], dtype=np.float32), shape)
    assert tracker.previous_box is None


def test_segmentation_method_roi_crossing_render_and_five_frame_vote():
    roi = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    # The mock returns a letterboxed mask so the detector's mask-to-frame conversion is exercised.
    safe_mask = np.zeros((384, 480), dtype=np.float32)
    safe_mask[120:260, 150:320] = 1.0

    # A small area crossing the right boundary is still unsafe because it exceeds 5%.
    exit_mask = np.zeros((384, 480), dtype=np.float32)
    exit_mask[140:220, 180:280] = 1.0
    exit_mask[140:200, 400:440] = 1.0

    empty_mask = np.zeros((384, 480), dtype=np.float32)

    model = _FakeYoloModel(
        [
            _FakeResult(boxes=[[200, 140, 420, 330]], conf=[0.85], masks=[safe_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.80], masks=[exit_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.81], masks=[exit_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.82], masks=[safe_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.83], masks=[exit_mask]),
            _FakeResult(boxes=[[240, 170, 580, 290]], conf=[0.75], masks=[empty_mask]),
        ]
    )
    detector = SegmentationBedExitMethod(
        roi_config=roi,
        outside_threshold=0.05,
        device='cuda:0',
        mask_max_dim=160,
        model=model,
    )
    state = BedExitState()
    frame = _make_frame(480, 640)
    frame_snapshot = frame.copy()

    # The state layer must wait for a complete five-frame window before publishing True.
    voted = [state.update(detector.judge(frame)) for _ in range(4)]
    assert voted == [False, False, False, False]
    assert np.array_equal(frame, frame_snapshot)
    assert model.calls[0]['device'] == 'cuda:0'
    assert model.calls[0]['retina_masks'] is False

    # The fifth frame completes [False, True, True, False, True], so 3/5 is unsafe.
    assert state.update(detector.judge(frame)) is True
    assert detector.last_result.outside_ratio > 0.05

    rendered = detector.render(frame)
    # Rendering must be non-destructive to inference input and produce a visible overlay.
    assert rendered.shape == frame.shape and not np.array_equal(rendered, frame)
    overlay = cm.draw_overlay(frame, 5, [cm.MethodRunner(name='segmentation', detector=detector, last_voted=True)])
    assert overlay.shape == frame.shape

    # Missing usable segmentation is an error so the node can reset its vote history.
    with pytest.raises(RuntimeError):
        detector.judge(frame)

    detector.close()


def test_zero_arg_load_via_bed_exit_method_loader(monkeypatch):
    # Stub the optional dependency to verify the loader's zero-argument construction path.
    created_calls = []

    class _StubYolo:
        def __init__(self, weights, task=None):
            created_calls.append((Path(weights).name, task))

    fake_ultralytics = types.ModuleType('ultralytics')
    fake_ultralytics.YOLO = _StubYolo
    monkeypatch.setitem(sys.modules, 'ultralytics', fake_ultralytics)

    seg = load_bed_exit_method('part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod')
    assert isinstance(seg, SegmentationBedExitMethod)
    # The loader must select the segmentation task and the package's default weights.
    assert created_calls == [('yolo11n-seg.pt', 'segment')]
    seg.close()


def test_default_camera_index_prefers_external_usb_capture_node(tmp_path):
    for dev, idx_str, name in [
        ('video0', '0', 'Integrated Camera: Integrated C'),
        ('video1', '1', 'Integrated Camera: Integrated C'),
        ('video2', '0', 'USB Composite Device: USB Camer'),
        ('video3', '1', 'USB Composite Device: USB Camer'),
    ]:
        dev_dir = tmp_path / dev
        dev_dir.mkdir()
        (dev_dir / 'index').write_text(idx_str, encoding='utf-8')
        (dev_dir / 'name').write_text(name, encoding='utf-8')

    assert default_camera_index(tmp_path) == 2
    assert default_camera_index(tmp_path / 'nonexistent') == 0
