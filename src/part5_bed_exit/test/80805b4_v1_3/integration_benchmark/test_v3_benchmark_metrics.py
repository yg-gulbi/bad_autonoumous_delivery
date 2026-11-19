"""v3 벤치마크의 타임라인 해석과 선택 지표를 검증한다."""

from pathlib import Path

import pytest

from research.integration_benchmark.metrics import load_timeline, selection_metrics


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
TIMELINE = PACKAGE_ROOT / 'research/configs/test_input_sequence1_timeline_v1.json'


def test_v3_timeline_labels_all_1024_frames():
    labels, events, _ = load_timeline(TIMELINE, 1024)
    assert labels.count(False) == 353
    assert labels.count(True) == 671
    assert len(events) == 9


def test_v3_selection_metrics_include_invalid_exit_as_miss():
    expected = [False, False, True, True, True, False]
    valid = [True, True, True, True, False, True]
    outputs = [False, True, False, True, None, False]
    rows = [
        {
            'frame_index': index,
            'expected_bool': expected[index],
            'valid': valid[index],
            'output_bool': outputs[index],
        }
        for index in range(6)
    ]
    metrics = selection_metrics(
        rows, [{'start_frame': 2, 'end_frame': 5}], fps=10.0
    )

    assert metrics['recall'] == pytest.approx(1 / 3)
    assert metrics['safe_fpr'] == pytest.approx(1 / 3)
    assert metrics['precision'] == pytest.approx(1 / 2)
    assert metrics['f1'] == pytest.approx(0.4)
    assert metrics['coverage'] == pytest.approx(5 / 6)
    assert metrics['event_detected'] == 1
    assert metrics['event_latency_mean_s'] == pytest.approx(0.1)
