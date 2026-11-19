"""단위 테스트: 설정 기반 판단 객체 팩토리를 검증한다.

대상 코드는 ``pretrained.create_method()``이다. 실제 가중치를 읽지 않고 네 모델
클래스를 가짜 생성자로 바꿔, 운영에서는 JSON의 ``method``를 사용하고 연구 비교에서는
명시한 모델 이름으로 선택값을 덮어쓰는지 확인한다.
"""

from pathlib import Path

import pytest

from part5_bed_exit import pretrained


def test_runtime_factory_uses_method_selected_in_config(monkeypatch):
    """운영에서는 설정 JSON의 method를 선택한다."""
    roi = object()
    calls = []
    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: (
            {'method': 'segmentation', 'device': 'cpu'},
            roi,
            Path('/models'),
        ),
    )

    def fake_segmentation_roi(selected_roi, weights, **kwargs):
        calls.append((selected_roi, weights, kwargs))
        return object()

    monkeypatch.setattr(pretrained, 'SegmentationROI', fake_segmentation_roi)
    pretrained.create_method('methods.json')

    selected_roi, weights, kwargs = calls[0]
    assert selected_roi is roi
    assert weights == Path('/models/yolo11n-seg.pt')
    assert kwargs['device'] == 'cpu'
    assert kwargs['confidence'] == 0.5
    assert kwargs['outside_fraction'] == 0.05


def test_method_override_uses_one_explicit_branch_per_model(monkeypatch):
    """연구 비교에서는 지정한 이름으로 설정의 method를 덮어쓴다."""
    roi = object()
    calls = []
    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: ({'method': 'segmentation', 'device': 'cpu'}, roi, Path('/models')),
    )

    def constructor(name):
        def create(selected_roi, weights, **kwargs):
            calls.append((name, selected_roi, weights, kwargs))
            return object()
        return create

    monkeypatch.setattr(pretrained, 'DetectionROI', constructor('detection'))
    monkeypatch.setattr(pretrained, 'SegmentationROI', constructor('segmentation'))
    monkeypatch.setattr(pretrained, 'YoloPoseROI', constructor('yolo_pose'))
    monkeypatch.setattr(pretrained, 'MediaPipePoseROI', constructor('mediapipe_pose'))

    for name in pretrained.SUPPORTED_METHODS:
        pretrained.create_method('methods.json', name)

    assert [name for name, *_ in calls] == list(pretrained.SUPPORTED_METHODS)
    assert [weights.name for _, _, weights, _ in calls] == [
        'yolo11n.pt',
        'yolo11n-seg.pt',
        'yolo11n-pose.pt',
        'pose_landmarker_full.task',
    ]

    with pytest.raises(ValueError, match='method_config'):
        pretrained.create_method('', 'detection')
    with pytest.raises(ValueError, match='Unknown method'):
        pretrained.create_method('methods.json', 'unknown')
