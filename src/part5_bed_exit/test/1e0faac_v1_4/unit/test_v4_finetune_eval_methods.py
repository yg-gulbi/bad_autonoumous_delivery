"""목적: ``finetune/eval_methods`` 비교 메서드들의 동적 로딩·좌표 회전·환경변수 오버라이드를 검증한다."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.methods import bed_exit_core


FINETUNE_ROOT = Path(__file__).resolve().parents[3] / 'finetune'


class _FakeTensor:
    def __init__(self, array):
        self._array = np.asarray(array, dtype=np.float32)

    def cpu(self):
        return self

    def numpy(self):
        return self._array


class _FakeYoloModel:
    def __init__(self, *, with_mask=True, with_keypoints=False):
        self.seen_shapes = []
        self.with_mask = with_mask
        self.with_keypoints = with_keypoints

    def predict(self, source, **kwargs):
        self.seen_shapes.append(source.shape)
        h, w = source.shape[:2]
        box = [w * 0.25, h * 0.25, w * 0.75, h * 0.75]
        masks = None
        if self.with_mask:
            mask = np.zeros((1, h, w), dtype=np.float32)
            mask[0, int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)] = 1.0
            masks = SimpleNamespace(data=_FakeTensor(mask))
        keypoints = None
        if self.with_keypoints:
            pts = np.zeros((1, 17, 3), dtype=np.float32)
            pts[0, :, 0] = w * 0.5
            pts[0, :, 1] = h * 0.5
            pts[0, :, 2] = 0.9
            keypoints = SimpleNamespace(data=_FakeTensor(pts))
        result = SimpleNamespace(
            boxes=SimpleNamespace(
                xyxy=_FakeTensor([box]),
                conf=_FakeTensor([0.92]),
            ),
            masks=masks,
            keypoints=keypoints,
        )
        return [result]


def _write_sample_config(tmp_path):
    models_dir = tmp_path / 'models'
    models_dir.mkdir()
    for fname in ('yolo11n-seg.pt', 'custom_seg.pt', 'custom_det.pt', 'custom_pose.pt'):
        (models_dir / fname).write_bytes(b'dummy')
    cfg_path = tmp_path / 'cam_roi.json'
    cfg_path.write_text(
        json.dumps({
            'method': 'segmentation',
            'model_dir': str(models_dir),
            'device': 'cpu',
            'roi': [[0.1, 0.2], [0.9, 0.2], [0.9, 0.8], [0.1, 0.8]],
        }),
        encoding='utf-8',
    )
    return cfg_path, models_dir


def test_finetune_rot90_segmentation_rotates_frame_and_roi(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(FINETUNE_ROOT))
    from eval_methods import base as eval_base

    assert eval_base.rotate_vertices_ccw90([[1.0, 0.5], [0.0, 0.5]]) == [
        [0.5, 0.0],
        [0.5, 1.0],
    ]

    cfg_path, models_dir = _write_sample_config(tmp_path)
    fake_model = _FakeYoloModel(with_mask=True)
    loaded = []

    def fake_yolo_init(self, roi, weights, task, device='cpu',
                       confidence=0.5, imgsz=640, model=None):
        loaded.append((weights, task, device, confidence))
        bed_exit_core.BedExitMethod.__init__(self, roi)
        self.model = fake_model
        self.device, self.confidence, self.imgsz = device, confidence, imgsz

    monkeypatch.setattr(bed_exit_core.YoloMethod, '__init__', fake_yolo_init)
    monkeypatch.setenv('PART5_EVAL_CONFIG', str(cfg_path))
    monkeypatch.setenv('PART5_EVAL_WEIGHTS', 'custom_seg.pt')
    monkeypatch.setenv('PART5_EVAL_DEVICE', 'cuda:0')
    monkeypatch.setenv('PART5_EVAL_CONF', '0.35')
    monkeypatch.setenv('PART5_EVAL_OUTSIDE', '0.12')
    monkeypatch.setenv('PART5_EVAL_MIN_IOU', '0.10')

    detector = load_bed_exit_method('eval_methods.rot90_seg:Rot90Segmentation')
    assert loaded == [(models_dir / 'custom_seg.pt', 'segment', 'cuda:0', 0.35)]
    assert detector.outside_fraction == 0.12
    assert detector.selector.min_iou == 0.10

    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    assert detector.judge(frame) is False
    assert fake_model.seen_shapes == [(64, 48, 3)]
    assert detector.render(frame).shape == (64, 48, 3)


def test_finetune_baseline_det_and_pose_methods_load(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(FINETUNE_ROOT))
    cfg_path, _ = _write_sample_config(tmp_path)
    loaded = []

    def fake_yolo_init(self, roi, weights, task, device='cpu',
                       confidence=0.5, imgsz=640, model=None):
        loaded.append((weights.name, task))
        bed_exit_core.BedExitMethod.__init__(self, roi)
        self.model = _FakeYoloModel(
            with_mask=(task == 'segment'),
            with_keypoints=(task == 'pose'),
        )
        self.device, self.confidence, self.imgsz = device, confidence, imgsz

    monkeypatch.setattr(bed_exit_core.YoloMethod, '__init__', fake_yolo_init)
    monkeypatch.setenv('PART5_EVAL_CONFIG', str(cfg_path))
    monkeypatch.delenv('PART5_EVAL_WEIGHTS', raising=False)
    monkeypatch.delenv('PART5_EVAL_DEVICE', raising=False)

    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    base_seg = load_bed_exit_method('eval_methods.baseline_seg:BaselineSegmentation')
    assert base_seg.judge(frame) is False
    assert base_seg.model.seen_shapes == [(48, 64, 3)]

    monkeypatch.setenv('PART5_EVAL_WEIGHTS', 'custom_det.pt')
    rot_det = load_bed_exit_method('eval_methods.rot90_det:Rot90Detection')
    assert rot_det.judge(frame) is False
    assert rot_det.model.seen_shapes == [(64, 48, 3)]

    monkeypatch.setenv('PART5_EVAL_WEIGHTS', 'custom_pose.pt')
    rot_pose = load_bed_exit_method('eval_methods.rot90_pose:Rot90YoloPose')
    assert rot_pose.judge(frame) is False
    assert rot_pose.model.seen_shapes == [(64, 48, 3)]

    assert loaded == [
        ('yolo11n-seg.pt', 'segment'),
        ('custom_det.pt', 'detect'),
        ('custom_pose.pt', 'pose'),
    ]
