"""v3 벤치마크가 판단 객체를 런타임 로더로 생성하는지 검증한다."""

from types import SimpleNamespace

import pytest

from research.integration_benchmark import benchmark


METHODS = ('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose')


def test_all_builtin_methods_are_created_through_runtime_loader(monkeypatch):
    calls = []

    def fake_loader(method, config):
        calls.append((method, config))
        return SimpleNamespace(judge=lambda frame: False)

    monkeypatch.setattr(benchmark, 'load_bed_exit_method', fake_loader)
    detectors = benchmark.load_benchmark_methods(
        METHODS, 'methods.json', pose_loss_after_track=False
    )

    assert list(detectors) == list(METHODS)
    assert calls == [(method, 'methods.json') for method in METHODS]


def test_pose_loss_wrapper_is_applied_after_runtime_loading(monkeypatch):
    detector = SimpleNamespace(judge=lambda frame: False)
    monkeypatch.setattr(
        benchmark, 'load_bed_exit_method', lambda method, config: detector
    )

    loaded = benchmark.load_benchmark_methods(
        ['yolo_pose'], 'methods.json', pose_loss_after_track=True
    )

    assert isinstance(loaded['yolo_pose'], benchmark.PoseLossAfterTrack)
    assert loaded['yolo_pose'].detector is detector


def test_partial_load_failure_closes_already_created_methods(monkeypatch):
    first = SimpleNamespace(
        closed=False,
        close=lambda: setattr(first, 'closed', True),
    )

    def fake_loader(method, config):
        if method == 'segmentation':
            raise RuntimeError('load failed')
        return first

    monkeypatch.setattr(benchmark, 'load_bed_exit_method', fake_loader)

    with pytest.raises(RuntimeError, match='load failed'):
        benchmark.load_benchmark_methods(
            ['detection', 'segmentation'],
            'methods.json',
            pose_loss_after_track=False,
        )

    assert first.closed is True
