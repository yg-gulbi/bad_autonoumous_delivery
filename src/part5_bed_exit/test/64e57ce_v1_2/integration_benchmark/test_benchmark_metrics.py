"""목적: integration benchmark의 라벨 해석과 모델 선택 지표 계산 검증.

계획: 실제 1,024프레임 타임라인의 분포와 합성 프레임의 지표 공식을 확인한다.
입력: timeline_v1.json, SAFE/EXIT·유효/무효·Bool이 지정된 합성 행 6개.
예상 출력: SAFE 353, EXIT 671, 이벤트 9개와 수식별 정확한 비율.
실제 출력: PASS, 지표·타임라인 테스트 2개 통과.
"""

from pathlib import Path

import pytest

from research.integration_benchmark.metrics import load_timeline, selection_metrics


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
TIMELINE = PACKAGE_ROOT / 'research/configs/test_input_sequence1_timeline_v1.json'


def test_timeline_labels_all_1024_frames():
    labels, events, _ = load_timeline(TIMELINE, 1024)

    assert labels.count(False) == 353
    assert labels.count(True) == 671
    assert len(events) == 9


def test_selection_metrics_include_invalid_exit_as_miss():
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
    events = [{'start_frame': 2, 'end_frame': 5}]

    metrics = selection_metrics(rows, events, fps=10.0)

    assert metrics['recall'] == pytest.approx(1 / 3)
    assert metrics['safe_fpr'] == pytest.approx(1 / 3)
    assert metrics['precision'] == pytest.approx(1 / 2)
    assert metrics['f1'] == pytest.approx(0.4)
    assert metrics['coverage'] == pytest.approx(5 / 6)
    assert metrics['event_detected'] == 1
    assert metrics['event_latency_mean_s'] == pytest.approx(0.1)
