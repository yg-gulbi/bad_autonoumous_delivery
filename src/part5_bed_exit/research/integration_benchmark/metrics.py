"""ROS와 모델 의존성 없이 integration benchmark 선택 지표를 계산한다."""

import json
from pathlib import Path

import numpy as np


def load_timeline(path, total_frames):
    """연속 구간을 프레임별 Bool 라벨과 EXIT 이벤트 목록으로 변환한다."""
    path = Path(path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding='utf-8'))
    labels = [None] * total_frames
    exit_events = []
    for event in payload['events']:
        start = int(event['start_frame'])
        end = int(event['end_frame'])
        if start < 0 or end <= start or end > total_frames:
            raise ValueError(f'Invalid timeline range: {event}')
        value = bool(event['exit_hint'])
        for index in range(start, end):
            if labels[index] is not None:
                raise ValueError(f'Overlapping timeline frame: {index}')
            labels[index] = value
        if value:
            exit_events.append(event)
    if any(value is None for value in labels):
        raise ValueError('Timeline must label every input frame')
    return labels, exit_events, path


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def selection_metrics(rows, exit_events, fps):
    """Recall·SAFE FPR·Precision·F1·Coverage·이벤트 지연을 계산한다."""
    valid = [row for row in rows if row['valid']]
    tp = sum(row['expected_bool'] and row['output_bool'] is True for row in valid)
    fp = sum(not row['expected_bool'] and row['output_bool'] is True for row in valid)
    valid_safe = sum(not row['expected_bool'] for row in valid)
    exit_frames = sum(row['expected_bool'] for row in rows)
    fn = exit_frames - tp
    recall = _ratio(tp, tp + fn)
    precision = _ratio(tp, tp + fp)
    detected = 0
    latencies = []
    for event in exit_events:
        matches = [row for row in rows
                   if event['start_frame'] <= row['frame_index'] < event['end_frame']
                   and row['valid'] and row['output_bool'] is True]
        if matches:
            detected += 1
            latencies.append((matches[0]['frame_index'] - event['start_frame']) / fps)
    return {
        'recall': recall,
        'safe_fpr': _ratio(fp, valid_safe),
        'precision': precision,
        'f1': _ratio(2 * precision * recall, precision + recall)
        if precision is not None and recall is not None else None,
        'coverage': _ratio(len(valid), len(rows)),
        'tp': tp,
        'fp': fp,
        'fn': fn,
        'event_detected': detected,
        'event_total': len(exit_events),
        'event_detection_rate': _ratio(detected, len(exit_events)),
        'event_latency_mean_s': float(np.mean(latencies)) if latencies else None,
        'event_latency_p95_s': float(np.percentile(latencies, 95)) if latencies else None,
    }
