"""목적: ``pretrained.py``의 설정 해석과 내장 모델 팩터리를 검증한다.

대상은 ``read_config()``와 ``create_method()``이다. 모델 생성자는 Monkeypatch하여
가중치를 열지 않고 선택 분기와 생성자 입력만 확인한다.
"""

import json
from pathlib import Path

import pytest

from part5_bed_exit import pretrained
from part5_bed_exit.methods.bed_exit_core import PolygonROI


def test_read_config_resolves_model_dir_from_config_location(tmp_path):
    config_path = tmp_path / 'config' / 'methods.json'
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({
            'method': 'segmentation',
            'model_dir': '../models',
            'roi': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        }),
        encoding='utf-8',
    )

    config, roi, model_dir = pretrained.read_config(config_path)

    assert config['method'] == 'segmentation'
    assert isinstance(roi, PolygonROI)
    assert model_dir == (tmp_path / 'models').resolve()


def test_create_method_uses_method_from_config(monkeypatch):
    roi = object()
    calls = []
    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: ({'method': 'segmentation', 'device': 'cpu'}, roi, Path('/models')),
    )

    def create_segmentation(selected_roi, weights, **kwargs):
        calls.append((selected_roi, weights, kwargs))
        return 'segmentation-object'

    monkeypatch.setattr(pretrained, 'SegmentationROI', create_segmentation)

    assert pretrained.create_method('methods.json') == 'segmentation-object'
    selected_roi, weights, kwargs = calls[0]
    assert selected_roi is roi
    assert weights == Path('/models/yolo11n-seg.pt')
    assert kwargs == {
        'device': 'cpu',
        'confidence': 0.5,
        'outside_fraction': 0.05,
    }


def test_create_method_uses_explicit_override_for_each_builtin(monkeypatch):
    roi = object()
    calls = []
    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: ({'method': 'segmentation'}, roi, Path('/models')),
    )

    def constructor(name):
        def create(selected_roi, weights, **kwargs):
            calls.append((name, selected_roi, weights, kwargs))
            return name
        return create

    monkeypatch.setattr(pretrained, 'DetectionROI', constructor('detection'))
    monkeypatch.setattr(pretrained, 'SegmentationROI', constructor('segmentation'))
    monkeypatch.setattr(pretrained, 'YoloPoseROI', constructor('yolo_pose'))
    monkeypatch.setattr(pretrained, 'MediaPipePoseROI', constructor('mediapipe_pose'))

    results = [
        pretrained.create_method('methods.json', name)
        for name in pretrained.SUPPORTED_METHODS
    ]

    assert results == list(pretrained.SUPPORTED_METHODS)
    assert [weights.name for _, _, weights, _ in calls] == [
        'yolo11n.pt',
        'yolo11n-seg.pt',
        'yolo11n-pose.pt',
        'pose_landmarker_full.task',
    ]


def test_create_method_rejects_missing_config_and_unknown_method(monkeypatch):
    with pytest.raises(ValueError, match='method_config'):
        pretrained.create_method('')

    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: ({'method': 'unknown'}, object(), Path('/models')),
    )
    with pytest.raises(ValueError, match='Unknown method'):
        pretrained.create_method('methods.json')
