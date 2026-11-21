#!/usr/bin/env python3
"""Phase 3 Ensemble Benchmark on test_bed.mp4 (test-only).

Evaluates:
  3-A: Rotation TTA ensemble (0 deg + 90 deg CCW) using any_exit / all_exit
  3-B: Seg + Pose heterogeneous ensemble (SegmentationROI + YoloPoseROI)
  3-C: Lightweight rotated/custom seg + pretrained yolo11m-seg ensemble
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
from part5_bed_exit.methods.bed_exit_core import (
    JudgmentUnavailable,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
)
from part5_bed_exit.pretrained import (
    EnsembleBedExitMethod,
    read_config,
    rotate_frame_ccw,
    rotate_roi_ccw,
)
from research.integration_benchmark.metrics import load_timeline, selection_metrics


class RotatedDetectorWrapper:
    """Wrap a detector so judge(frame) runs on CCW-rotated frame and ROI."""

    def __init__(self, detector_cls, base_roi, weights: str, rotate_ccw: int, **kwargs):
        rot_roi = rotate_roi_ccw(base_roi, rotate_ccw)
        self.rotate_ccw = rotate_ccw
        self.detector = detector_cls(rot_roi, weights=weights, **kwargs)

    def judge(self, frame):
        rot_frame = rotate_frame_ccw(frame, self.rotate_ccw)
        return self.detector.judge(rot_frame)

    def close(self):
        close_fn = getattr(self.detector, 'close', None)
        if callable(close_fn):
            close_fn()


def eval_method(name: str, method, frames: list, labels: list[bool], exit_events: list[dict], fps: float) -> dict:
    state = BedExitState(window_size=5, threshold=3)
    frame_rows: list[dict] = []
    unavailable = 0
    t_start = time.perf_counter()

    for idx, frame in enumerate(frames):
        expected = bool(labels[idx])
        try:
            raw_bool = bool(method.judge(frame))
        except JudgmentUnavailable:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': expected, 'valid': False, 'output_bool': False})
            unavailable += 1
            continue
        frame_rows.append({'frame_index': idx, 'expected_bool': expected, 'valid': True, 'output_bool': bool(state.update(raw_bool))})

    elapsed = time.perf_counter() - t_start
    m = selection_metrics(frame_rows, exit_events, fps)
    def _r(val):
        return '' if val is None else round(float(val), 4)

    return {
        'ensemble': name,
        'fps': round(len(frames) / max(elapsed, 1e-6), 1),
        'coverage': _r(m['coverage']),
        'unavailable': unavailable,
        'recall': _r(m['recall']),
        'safe_fpr': _r(m['safe_fpr']),
        'precision': _r(m['precision']),
        'f1': _r(m['f1']),
        'events': f"{m['event_detected']}/{m['event_total']}",
        'latency_s': '' if m['event_latency_mean_s'] is None else round(m['event_latency_mean_s'], 3),
    }


def main():
    parser = argparse.ArgumentParser(description='Phase 3 Ensemble Benchmark.')
    parser.add_argument('--seg-n', default='src/part5_bed_exit/models/yolo11n-seg.pt')
    parser.add_argument('--seg-m', default='src/part5_bed_exit/models/yolo11m-seg.pt')
    parser.add_argument('--pose-n', default='src/part5_bed_exit/models/yolo11n-pose.pt')
    parser.add_argument('--custom-seg', default='', help='Optional fine-tuned COCO-rot seg .pt')
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/run_data/test_input_sequence1_methods_v1.json')
    parser.add_argument('--timeline', default='src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--confidence', type=float, default=0.5)
    parser.add_argument('--outside-fraction', type=float, default=0.05)
    parser.add_argument('--output-dir', default='')
    args = parser.parse_args()

    _, base_roi, _ = read_config(Path(args.config).resolve())
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

    seg_n = str(Path(args.seg_n).resolve())
    seg_m = str(Path(args.seg_m).resolve())
    pose_n = str(Path(args.pose_n).resolve())
    common = dict(device=args.device, confidence=args.confidence)

    candidates = [
        ('3A_rotTTA_any', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=seg_n, outside_fraction=args.outside_fraction, **common),
            RotatedDetectorWrapper(SegmentationROI, base_roi, seg_n, 90, outside_fraction=args.outside_fraction, **common),
        ], policy='any')),
        ('3A_rotTTA_all', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=seg_n, outside_fraction=args.outside_fraction, **common),
            RotatedDetectorWrapper(SegmentationROI, base_roi, seg_n, 90, outside_fraction=args.outside_fraction, **common),
        ], policy='all')),
        ('3B_SegN+PoseN_any', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=seg_n, outside_fraction=args.outside_fraction, **common),
            YoloPoseROI(base_roi, weights=pose_n, **common),
        ], policy='any')),
        ('3B_SegN+PoseN_all', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=seg_n, outside_fraction=args.outside_fraction, **common),
            YoloPoseROI(base_roi, weights=pose_n, **common),
        ], policy='all')),
    ]

    primary_w = str(Path(args.custom_seg).resolve()) if args.custom_seg else seg_n
    candidates.extend([
        ('3C_Seg1+SegM_any', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=primary_w, outside_fraction=args.outside_fraction, **common),
            SegmentationROI(base_roi, weights=seg_m, outside_fraction=args.outside_fraction, **common),
        ], policy='any')),
        ('3C_Seg1+SegM_all', lambda: EnsembleBedExitMethod([
            SegmentationROI(base_roi, weights=primary_w, outside_fraction=args.outside_fraction, **common),
            SegmentationROI(base_roi, weights=seg_m, outside_fraction=args.outside_fraction, **common),
        ], policy='all')),
    ])

    rows = []
    for name, builder in candidates:
        ens = builder()
        row = eval_method(name, ens, frames, labels, exit_events, fps)
        ens.close()
        rows.append(row)
        print(f"[{name:18s}] fps={row['fps']:5.1f} cov={row['coverage']:.3f} rec={row['recall']:.3f} "
              f"fpr={row['safe_fpr']:.3f} f1={row['f1']:.3f} ev={row['events']}", flush=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = Path(args.output_dir).resolve() if args.output_dir else (
        Path(__file__).resolve().parent / 'runs' / 'eval' / f'phase3_ensemble_{stamp}'
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / 'phase3_ensemble_results.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f'\n[SAVED] {csv_path}')


if __name__ == '__main__':
    main()
