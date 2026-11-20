"""목적: ``finetune/`` 파이프라인(폴리곤 회전, YOLO-seg 라벨 포맷, 벤치마크 루프)을 단위 검증한다."""

import importlib.util
from pathlib import Path

import numpy as np

from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable


FINETUNE_DIR = Path(__file__).resolve().parents[3] / 'finetune'


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, FINETUNE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_four_way_polygon_rotation_round_trip_and_direction():
    prep = _load_module('autolabel_test_bed', '01_autolabel_test_bed.py')
    pts = np.array([[1.0, 0.5], [0.0, 0.5], [0.25, 0.75]])

    # 90도 CCW: 오른쪽(1, 0.5) -> 위쪽(0.5, 0.0), 왼쪽(0, 0.5) -> 아래쪽(0.5, 1.0)
    rot90 = prep.rotate_norm_points_ccw(pts, 90)
    assert np.allclose(rot90[0], [0.5, 0.0])
    assert np.allclose(rot90[1], [0.5, 1.0])

    # 90도 회전 + 270도 역회전 == 항등
    restored = prep.rotate_norm_points_ccw(rot90, 270)
    assert np.allclose(restored, pts)

    # 180도 두 번 == 항등
    rot180 = prep.rotate_norm_points_ccw(pts, 180)
    assert np.allclose(prep.rotate_norm_points_ccw(rot180, 180), pts)


def test_format_seg_lines_and_timeline_lookup():
    prep = _load_module('autolabel_test_bed', '01_autolabel_test_bed.py')

    poly = np.array([[0.2, 0.3], [0.8, 0.3], [0.8, 0.7], [0.2, 0.7]])
    text = prep.format_seg_lines([poly])
    assert text.startswith('0 0.200000 0.300000 ')
    assert text.endswith('\n')

    events = [{'start_frame': 10, 'end_frame': 20, 'phase': 'exit_test', 'exit_hint': True}]
    assert prep.lookup_timeline(events, 15) == ('exit_test', 'EXIT')
    assert prep.lookup_timeline(events, 25) == ('unknown', 'UNKNOWN')


def test_benchmark_seg_models_evaluates_voting_and_unavailable_frames():
    bench = _load_module('bench_seg', '03_benchmark_seg_models.py')

    class _FakeDetector:
        def __init__(self, script):
            self._script = list(script)
            self._idx = 0
            self.last = type('Diag', (), {'reason': 'mask_outside_fraction'})()

        def judge(self, frame):
            val = self._script[self._idx]
            self._idx += 1
            if isinstance(val, Exception):
                raise val
            return val

    # 10프레임: 0~4 정상(False), 5~9 이탈(True), 5프레임 중 3회 다수결
    script = [False, False, False, False, False, True, True, True, JudgmentUnavailable('no_person'), True]
    labels = [False, False, False, False, False, True, True, True, True, True]
    events = [{'start_frame': 5, 'end_frame': 10}]
    frames = [(i, np.zeros((16, 16, 3), dtype=np.uint8)) for i in range(10)]

    rows, metrics, reason_counts = bench.evaluate_detector_on_frames(
        _FakeDetector(script), frames, labels, events, fps=10.0,
    )

    assert len(rows) == 10
    assert metrics['valid_frames'] == 9
    assert metrics['invalid_frames'] == 1
    assert metrics['no_person_frames'] == 1
    assert reason_counts['no_person'] == 1
    # 프레임 7(True 3번째)에서 5프레임 다수결 True 확정 -> latency = (7-5)/10 = 0.2s
    assert rows[7]['output_bool'] is True
    assert metrics['event_detected'] == 1
    assert metrics['event_latency_mean_s'] == 0.2
    assert metrics['safe_fpr'] == 0.0
