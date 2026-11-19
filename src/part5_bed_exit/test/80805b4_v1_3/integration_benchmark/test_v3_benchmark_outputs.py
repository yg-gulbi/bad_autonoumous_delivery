"""v3 개별 4모델·전체 벤치마크의 실제 산출물을 검증한다."""

import csv
import json
import os
from pathlib import Path

import pytest
import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = PACKAGE_ROOT / 'results/80805b4_v1_3/integration_benchmark'
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
def test_v3_completed_benchmark_outputs_match_runtime_loader_run():
    if os.environ.get('PART5_VERIFY_V3_BENCHMARK_OUTPUTS') != '1':
        pytest.skip('Run all v3 benchmarks and set PART5_VERIFY_V3_BENCHMARK_OUTPUTS=1')

    all_run = json.loads((RESULT_ROOT / 'all_modules/run.json').read_text(encoding='utf-8'))
    all_rows = {
        row['method']: row
        for row in _rows(RESULT_ROOT / 'all_modules/comparison.csv')
    }
    assert all_run['status'] == 'complete'
    assert all_run['frames'] == 1024
    assert all_run['method_loader'].endswith('.load_bed_exit_method')
    assert set(all_rows) == set(METHODS)

    valid_total = 0
    for method in METHODS:
        root = RESULT_ROOT / 'individual' / method
        run = json.loads((root / 'run.json').read_text(encoding='utf-8'))
        row = _rows(root / 'comparison.csv')[0]
        assert run['status'] == 'complete'
        assert run['frames'] == 1024
        assert run['method_loader'] == all_run['method_loader']
        assert row['method'] == method
        assert all(row[field] == all_rows[method][field] for field in ACCURACY_FIELDS)
        valid = int(row['valid_frames'])
        valid_total += valid
        assert _bag_count(root / 'rosbag/metadata.yaml') == valid * 2

    final_messages = int(all_rows['yolo_pose']['valid_frames'])
    assert _bag_count(RESULT_ROOT / 'all_modules/rosbag/metadata.yaml') == (
        valid_total + final_messages
    )
