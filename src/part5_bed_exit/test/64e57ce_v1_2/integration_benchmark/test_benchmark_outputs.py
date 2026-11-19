"""목적: 개별 4모델·전체 4모델 벤치마크의 실제 산출물 검증.

계획: run.json·comparison.csv·rosbag metadata를 읽어 실행 완료와 메시지 수를 대조한다.
입력: results/64e57ce/integration_benchmark 아래 개별 4회와 전체 1회 결과.
예상 출력: 5회 모두 1,024프레임 완료, 개별/전체 정확도 동일, bag 수 일치.
실제 출력: PASS, 개별 4회·전체 1회 완료, 전체 rosbag 5,041메시지.
"""

import csv
import json
import os
from pathlib import Path

import pytest
import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = PACKAGE_ROOT / 'results/64e57ce/integration_benchmark'
METHODS = ('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose')
ACCURACY_FIELDS = (
    'recall', 'safe_fpr', 'precision', 'f1', 'coverage',
    'tp', 'fp', 'fn', 'event_detected', 'event_total',
)


def _rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def _bag_count(path):
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    return payload['rosbag2_bagfile_information']['message_count']


@pytest.mark.integration
def test_completed_benchmark_outputs_match():
    if os.environ.get('PART5_VERIFY_BENCHMARK_OUTPUTS') != '1':
        pytest.skip('Set PART5_VERIFY_BENCHMARK_OUTPUTS=1 after running benchmarks')

    all_run = json.loads((RESULT_ROOT / 'all_modules/run.json').read_text(encoding='utf-8'))
    all_rows = {row['method']: row for row in _rows(
        RESULT_ROOT / 'all_modules/comparison.csv')}
    assert all_run['status'] == 'complete'
    assert all_run['frames'] == 1024
    assert set(all_rows) == set(METHODS)

    valid_total = 0
    for method in METHODS:
        root = RESULT_ROOT / 'individual' / method
        run = json.loads((root / 'run.json').read_text(encoding='utf-8'))
        row = _rows(root / 'comparison.csv')[0]
        assert run['status'] == 'complete'
        assert run['frames'] == 1024
        assert row['method'] == method
        assert all(row[field] == all_rows[method][field] for field in ACCURACY_FIELDS)
        valid = int(row['valid_frames'])
        valid_total += valid
        assert _bag_count(root / 'rosbag/metadata.yaml') == valid * 2

    final_yolo_messages = int(all_rows['yolo_pose']['valid_frames'])
    assert _bag_count(RESULT_ROOT / 'all_modules/rosbag/metadata.yaml') == (
        valid_total + final_yolo_messages)
    assert valid_total + final_yolo_messages == 5041

