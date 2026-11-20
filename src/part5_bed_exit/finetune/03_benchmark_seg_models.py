#!/usr/bin/env python3
"""Step 3: 베이스라인 및 파인튜닝 Segmentation 모델들을 동일 Part 5 파이프라인으로 공정 벤치마크한다.

실제 운영 코드와 완전히 동일한 ``SegmentationROI`` + ``BedExitState()`` (5프레임 중 3회 다수결,
예외 시 이력 초기화·미발행) 및 ``research/integration_benchmark/metrics.py``의 공식 선택 지표를
사용해 여러 ``.pt`` 후보를 한 번에 비교한다.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
import time

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import cv2
import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import (
    JudgmentUnavailable,
    PolygonROI,
    SegmentationROI,
    YOLO_CONFIDENCE,
)
from part5_bed_exit.pretrained import read_config
from research.integration_benchmark.metrics import load_timeline, selection_metrics


SUMMARY_FIELDS = [
    'label', 'weights', 'device', 'rotate_ccw90',
    'valid_frames', 'invalid_frames', 'no_person_frames', 'coverage',
    'recall', 'safe_fpr', 'precision', 'f1',
    'tp', 'fp', 'fn',
    'event_detected', 'event_total', 'event_detection_rate',
    'event_latency_mean_s', 'event_latency_p95_s',
    'judge_p95_ms', 'processing_fps',
]

FRAME_FIELDS = [
    'frame_index', 'media_s', 'expected_bool', 'valid',
    'raw_bool', 'output_bool', 'history_size', 'judge_ms', 'reason',
]


def _rotate_vertices_ccw90(vertices):
    return [[float(y), float(1.0 - x)] for x, y in vertices]


def evaluate_detector_on_frames(
    detector,
    frames_iter,
    labels: list[bool],
    exit_events: list[dict],
    fps: float,
    *,
    rotate_ccw90: bool = False,
    video_writer=None,
) -> tuple[list[dict], dict, dict]:
    """단일 판단 객체를 전체 프레임과 5프레임 다수결에 통과시켜 공식 지표를 계산한다."""
    state = BedExitState()
    rows: list[dict] = []
    judge_ms_list: list[float] = []
    reason_counts: Counter = Counter()

    for frame_index, frame in frames_iter:
        work_frame = (
            cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
            if rotate_ccw90 else frame
        )
        t0 = time.perf_counter()
        try:
            raw_bool = detector.judge(work_frame)
            output_bool = state.update(raw_bool)
            valid = True
            reason = getattr(getattr(detector, 'last', None), 'reason', 'ok')
        except JudgmentUnavailable as exc:
            state = BedExitState()
            raw_bool = None
            output_bool = None
            valid = False
            reason = str(exc)
        except Exception as exc:
            state = BedExitState()
            raw_bool = None
            output_bool = None
            valid = False
            reason = f'{type(exc).__name__}: {exc}'
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        judge_ms_list.append(elapsed_ms)
        reason_counts[reason] += 1

        rows.append({
            'frame_index': frame_index,
            'media_s': round(frame_index / fps, 6),
            'expected_bool': labels[frame_index],
            'valid': valid,
            'raw_bool': raw_bool,
            'output_bool': output_bool,
            'history_size': len(state.history),
            'judge_ms': round(elapsed_ms, 3),
            'reason': reason,
        })

        if video_writer is not None:
            render = getattr(detector, 'render', None)
            canvas = render(work_frame) if callable(render) else work_frame.copy()
            h, w = canvas.shape[:2]
            status_txt = (
                f'NO DECISION ({reason})' if not valid
                else f'OUT={output_bool} RAW={raw_bool} GT={labels[frame_index]}'
            )
            color = (0, 165, 255) if not valid else ((0, 0, 255) if output_bool else (0, 220, 0))
            cv2.rectangle(canvas, (0, h - 32), (w, h), (20, 20, 20), -1)
            cv2.putText(
                canvas, f'f={frame_index} | {status_txt} | {elapsed_ms:.1f}ms',
                (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
            )
            video_writer.write(canvas)

    metrics = selection_metrics(rows, exit_events, fps)
    total_s = sum(judge_ms_list) / 1000.0
    valid_frames = sum(1 for r in rows if r['valid'])
    metrics.update({
        'valid_frames': valid_frames,
        'invalid_frames': len(rows) - valid_frames,
        'no_person_frames': reason_counts.get('no_person', 0),
        'judge_p95_ms': float(np.percentile(judge_ms_list, 95)) if judge_ms_list else None,
        'processing_fps': (len(rows) / total_s) if total_s > 0 else None,
    })
    return rows, metrics, dict(reason_counts)


def parse_candidate_arg(spec: str) -> dict:
    """``label=weights_path`` 또는 ``label=weights_path:rot90`` 문자열을 파싱한다."""
    if '=' not in spec:
        raise ValueError(f'Candidate must use format label=weights[:rot90], got {spec!r}')
    label, rest = spec.split('=', 1)
    rotate_ccw90 = False
    if rest.endswith(':rot90'):
        rotate_ccw90 = True
        rest = rest[:-6]
    return {
        'label': label.strip(),
        'weights': rest.strip(),
        'rotate_ccw90': rotate_ccw90,
    }


def load_candidates(args) -> list[dict]:
    candidates: list[dict] = []
    if args.spec:
        spec_path = Path(args.spec).expanduser().resolve()
        payload = json.loads(spec_path.read_text(encoding='utf-8'))
        for item in payload.get('candidates', []):
            candidates.append({
                'label': item['label'],
                'weights': item['weights'],
                'rotate_ccw90': bool(item.get('rotate_ccw90', False)),
                'confidence': float(item.get('confidence', YOLO_CONFIDENCE)),
                'outside_fraction': float(item.get('outside_fraction', 0.05)),
                'optional': bool(item.get('optional', False)),
            })
    for raw in args.candidate:
        item = parse_candidate_arg(raw)
        item.setdefault('confidence', args.confidence)
        item.setdefault('outside_fraction', args.outside_fraction)
        item.setdefault('optional', False)
        candidates.append(item)
    if not candidates:
        raise ValueError('Provide at least one --candidate or --spec file')
    return candidates


def _resolve_weights_input(weights_str: str, workspace_root: Path) -> str:
    """로컬 파일 경로가 있으면 절대경로로, 공식 모델명이면 finetune/data/pretrained 아래로 해석한다."""
    path = Path(weights_str).expanduser()
    if path.is_file():
        return str(path.resolve())
    rel = workspace_root / path
    if rel.is_file():
        return str(rel.resolve())
    if '/' not in weights_str and not weights_str.startswith('.'):
        cache_dir = Path(__file__).resolve().parent / 'data' / 'pretrained'
        cache_dir.mkdir(parents=True, exist_ok=True)
        return str(cache_dir / weights_str)
    return weights_str


def _format_num(val, digits=4):
    if val is None:
        return '-'
    if isinstance(val, int):
        return str(val)
    return f'{val:.{digits}f}'


def print_markdown_table(summaries: list[dict]):
    headers = [
        'Model', 'Rot', 'FPS', 'p95(ms)', 'Coverage', 'no_person',
        'Recall', 'SAFE_FPR', 'Precision', 'F1', 'Events', 'Latency(s)',
    ]
    print('\n| ' + ' | '.join(headers) + ' |')
    print('| ' + ' | '.join(['---'] * len(headers)) + ' |')
    for s in summaries:
        row = [
            s['label'],
            'ccw90' if s['rotate_ccw90'] else '0',
            _format_num(s['processing_fps'], 1),
            _format_num(s['judge_p95_ms'], 1),
            _format_num(s['coverage'], 3),
            str(s['no_person_frames']),
            _format_num(s['recall'], 3),
            _format_num(s['safe_fpr'], 3),
            _format_num(s['precision'], 3),
            _format_num(s['f1'], 3),
            f'{s["event_detected"]}/{s["event_total"]}',
            _format_num(s['event_latency_mean_s'], 3),
        ]
        print('| ' + ' | '.join(row) + ' |')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Benchmark baseline and fine-tuned Segmentation models on Part 5 pipeline.',
    )
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/run_data/test_input_sequence1_methods_v1.json')
    parser.add_argument('--timeline', default='src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--spec', default='', help='JSON file defining candidate list')
    parser.add_argument('--candidate', action='append', default=[],
                        help='Candidate in format label=weights.pt[:rot90] (repeatable)')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--confidence', type=float, default=YOLO_CONFIDENCE)
    parser.add_argument('--outside-fraction', type=float, default=0.05)
    parser.add_argument('--max-frames', type=int, default=None)
    parser.add_argument('--save-video', action='store_true')
    parser.add_argument('--output-dir', default='')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    workspace_root = Path(__file__).resolve().parents[3]
    video_path = Path(args.video).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    timeline_path = Path(args.timeline).expanduser().resolve()

    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    _, base_roi, _ = read_config(config_path)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {video_path}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    labels, exit_events, _ = load_timeline(timeline_path, total_frames)
    if args.max_frames is not None:
        labels = labels[:args.max_frames]
        exit_events = [e for e in exit_events if e['start_frame'] < args.max_frames]

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (Path(__file__).resolve().parent / 'runs' / f'bench_{stamp}')
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = load_candidates(args)
    summaries: list[dict] = []
    details: dict[str, dict] = {}

    from ultralytics import YOLO

    for item in candidates:
        label = item['label']
        weights_resolved = _resolve_weights_input(item['weights'], workspace_root)
        is_local_path = '/' in item['weights'] or item['weights'].startswith('.')
        if is_local_path and not Path(weights_resolved).is_file():
            if item.get('optional'):
                print(f'[SKIP] Optional candidate not yet trained: {label} ({weights_resolved})', flush=True)
                continue
            raise FileNotFoundError(f'Weights not found for {label}: {weights_resolved}')

        print(f'\n[BENCH START] {label} | weights={weights_resolved} | device={args.device} | rot90={item["rotate_ccw90"]}', flush=True)
        roi = (
            PolygonROI(_rotate_vertices_ccw90(base_roi.vertices))
            if item['rotate_ccw90'] else base_roi
        )
        # YOLO(...)가 공식 모델명 자동 다운로드와 로컬 .pt 경로를 모두 지원하도록 모델 인스턴스 주입
        yolo_model = YOLO(weights_resolved, task='segment')
        detector = SegmentationROI(
            roi,
            weights=weights_resolved,
            outside_fraction=item['outside_fraction'],
            device=args.device,
            confidence=item['confidence'],
            model=yolo_model,
        )

        writer = None
        if args.save_video:
            vid_w, vid_h = (height, width) if item['rotate_ccw90'] else (width, height)
            writer = cv2.VideoWriter(
                str(out_dir / f'overlay_{label}.mp4'),
                cv2.VideoWriter_fourcc(*'mp4v'),
                fps,
                (vid_w, vid_h),
            )

        def _frame_stream():
            c = cv2.VideoCapture(str(video_path))
            try:
                idx = 0
                limit = args.max_frames or total_frames
                while idx < limit:
                    ok, frame = c.read()
                    if not ok or frame is None:
                        break
                    yield idx, frame
                    idx += 1
            finally:
                c.release()

        try:
            rows, metrics, reason_counts = evaluate_detector_on_frames(
                detector,
                _frame_stream(),
                labels,
                exit_events,
                fps,
                rotate_ccw90=item['rotate_ccw90'],
                video_writer=writer,
            )
        finally:
            if writer is not None:
                writer.release()
            detector.close()

        row_summary = {
            'label': label,
            'weights': weights_resolved,
            'device': args.device,
            'rotate_ccw90': item['rotate_ccw90'],
            **metrics,
        }
        summaries.append(row_summary)
        details[label] = {
            'summary': row_summary,
            'reason_counts': reason_counts,
        }

        with (out_dir / f'frames_{label}.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=FRAME_FIELDS)
            w.writeheader()
            w.writerows(rows)

        print(
            f'[BENCH END] {label} | fps={_format_num(metrics["processing_fps"], 1)} '
            f'cov={_format_num(metrics["coverage"], 3)} '
            f'recall={_format_num(metrics["recall"], 3)} '
            f'safe_fpr={_format_num(metrics["safe_fpr"], 3)} '
            f'f1={_format_num(metrics["f1"], 3)} '
            f'events={metrics["event_detected"]}/{metrics["event_total"]}',
            flush=True,
        )

    with (out_dir / 'comparison.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for row in summaries:
            w.writerow({k: row.get(k) for k in SUMMARY_FIELDS})

    (out_dir / 'summary.json').write_text(
        json.dumps({
            'video': str(video_path),
            'config': str(config_path),
            'timeline': str(timeline_path),
            'device': args.device,
            'candidates': details,
        }, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    print_markdown_table(summaries)
    print(f'\n[ARTIFACTS] {out_dir}', flush=True)


if __name__ == '__main__':
    main()
