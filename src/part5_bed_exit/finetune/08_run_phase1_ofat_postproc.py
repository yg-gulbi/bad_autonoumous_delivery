#!/usr/bin/env python3
"""Phase 1 OFAT Post-processing Limit Benchmark on test_bed.mp4 (test-only).

Starts from Step 0 Control Baseline (yolo11n-seg.pt, conf=0.5, mask_thr=0.5,
outside_fraction=0.05, vote=3/5) and varies ONE parameter at a time.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
import sys
import time

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import cv2

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable, SegmentationROI
from part5_bed_exit.pretrained import read_config
from research.integration_benchmark.metrics import load_timeline, selection_metrics


def run_single_config(
    frames: list,
    labels: list[bool],
    exit_events: list[dict],
    fps: float,
    roi,
    weights_path: str,
    yolo_model,
    *,
    group: str,
    tag: str,
    device: str,
    confidence: float,
    mask_threshold: float,
    outside_fraction: float,
    history_size: int,
    min_votes: int,
) -> dict:
    detector = SegmentationROI(
        roi,
        weights=weights_path,
        outside_fraction=outside_fraction,
        mask_threshold=mask_threshold,
        device=device,
        confidence=confidence,
        model=yolo_model,
    )
    state = BedExitState(window_size=history_size, threshold=min_votes)

    frame_rows: list[dict] = []
    no_person = 0
    t_start = time.perf_counter()

    for idx, frame in enumerate(frames):
        expected = bool(labels[idx])
        try:
            raw_bool = bool(detector.judge(frame))
        except JudgmentUnavailable as exc:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': expected, 'valid': False, 'output_bool': False})
            if 'no_person' in str(exc):
                no_person += 1
            continue
        out_bool = bool(state.update(raw_bool))
        frame_rows.append({'frame_index': idx, 'expected_bool': expected, 'valid': True, 'output_bool': out_bool})

    elapsed = time.perf_counter() - t_start
    m = selection_metrics(frame_rows, exit_events, fps)
    def _r(val):
        return '' if val is None else round(float(val), 4)

    return {
        'group': group,
        'tag': tag,
        'conf': confidence,
        'mask_thr': mask_threshold,
        'outside_frac': outside_fraction,
        'vote': f'{min_votes}/{history_size}',
        'fps': round(len(frames) / max(elapsed, 1e-6), 1),
        'coverage': _r(m['coverage']),
        'no_person': no_person,
        'recall': _r(m['recall']),
        'safe_fpr': _r(m['safe_fpr']),
        'precision': _r(m['precision']),
        'f1': _r(m['f1']),
        'events': f"{m['event_detected']}/{m['event_total']}",
        'latency_s': '' if m['event_latency_mean_s'] is None else round(m['event_latency_mean_s'], 3),
    }


def main():
    parser = argparse.ArgumentParser(description='Phase 1 OFAT post-processing benchmark.')
    parser.add_argument('--weights', default='src/part5_bed_exit/models/yolo11n-seg.pt')
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/run_data/test_input_sequence1_methods_v1.json')
    parser.add_argument('--timeline', default='src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--output-dir', default='')
    args = parser.parse_args()

    _, roi, _ = read_config(Path(args.config).resolve())
    cap = cv2.VideoCapture(str(Path(args.video).resolve()))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {args.video}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()

    labels, exit_events, _ = load_timeline(Path(args.timeline).resolve(), len(frames))

    weights_path = str(Path(args.weights).resolve())
    from ultralytics import YOLO
    yolo_model = YOLO(weights_path, task='segment')

    base = dict(confidence=0.5, mask_threshold=0.5, outside_fraction=0.05, history_size=5, min_votes=3)
    experiments = [
        ('Step0', 'baseline_default', base),
        *[('1-A_outside', f'out={v}', {**base, 'outside_fraction': v}) for v in (0.01, 0.02, 0.03, 0.08, 0.10)],
        *[('1-B_conf', f'conf={v}', {**base, 'confidence': v}) for v in (0.15, 0.25, 0.35)],
        *[('1-C_mask_thr', f'mask={v}', {**base, 'mask_threshold': v}) for v in (0.35, 0.60, 0.70)],
        *[('1-D_vote', f'vote={mv}/{hs}', {**base, 'min_votes': mv, 'history_size': hs})
          for mv, hs in ((2, 3), (4, 5), (5, 7), (6, 9))],
    ]

    rows = []
    for group, tag, kw in experiments:
        row = run_single_config(
            frames, labels, exit_events, fps, roi, weights_path, yolo_model,
            group=group, tag=tag, device=args.device, **kw,
        )
        rows.append(row)
        print(f"[{group:12s}] {tag:16s} | cov={row['coverage']:.3f} (no_p={row['no_person']:3d}) | "
              f"rec={row['recall']:.3f} fpr={row['safe_fpr']:.3f} f1={row['f1']:.3f} ev={row['events']}", flush=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = Path(args.output_dir).resolve() if args.output_dir else (
        Path(__file__).resolve().parent / 'runs' / f'phase1_ofat_{stamp}'
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / 'phase1_ofat_results.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f'\n[SAVED] {csv_path}')


if __name__ == '__main__':
    main()
