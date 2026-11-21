#!/usr/bin/env python3
"""Evaluate Notion 1.1 proposals on test_bed.mp4 (test-only).

Covers:
  1) Baseline yolo11n-seg vs yolo11m-seg + 2-angle (0/90) & 4-angle (0/90/180/270)
     pixel-level Rotation TTA (mask union / mask average).
  2) YOLO Tracking (ByteTrack persist=True) locking onto primary patient track ID.
  3) Farneback Optical Flow motion-gated exit check (suppressing static mask bleed).
  4) UNKNOWN (no_person / unavailable) streak & episode duration logging.
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
import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable, SegmentationROI
from part5_bed_exit.pretrained import read_config, rotate_frame_ccw
from research.integration_benchmark.metrics import load_timeline, selection_metrics


def _inv_rotate_mask(mask: np.ndarray, angle_ccw: int) -> np.ndarray:
    """Inverse-rotate a mask produced on a `rotate_frame_ccw(..., angle_ccw)` frame."""
    a = angle_ccw % 360
    if a == 0:
        return mask
    if a == 90:
        return cv2.rotate(mask, cv2.ROTATE_90_CLOCKWISE)
    if a == 180:
        return cv2.rotate(mask, cv2.ROTATE_180)
    if a == 270:
        return cv2.rotate(mask, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f'Unsupported angle: {angle_ccw}')


def _unknown_stats(valid_flags: list[bool], fps: float) -> tuple[int, int, float]:
    episodes = 0
    max_streak = 0
    cur = 0
    for v in valid_flags:
        if not v:
            if cur == 0:
                episodes += 1
            cur += 1
            if cur > max_streak:
                max_streak = cur
        else:
            cur = 0
    total_unknown = sum(1 for v in valid_flags if not v)
    return total_unknown, episodes, round(max_streak / max(fps, 1e-6), 3)


def _summarize(name: str, frame_rows: list[dict], exit_events: list[dict], fps: float, elapsed: float) -> dict:
    m = selection_metrics(frame_rows, exit_events, fps)
    valid_flags = [bool(r['valid']) for r in frame_rows]
    unk_frames, unk_ep, unk_max_s = _unknown_stats(valid_flags, fps)

    def _r(v):
        return '' if v is None else round(float(v), 4)

    return {
        'method': name,
        'fps': round(len(frame_rows) / max(elapsed, 1e-6), 1),
        'coverage': _r(m['coverage']),
        'unknown_frames': unk_frames,
        'unknown_episodes': unk_ep,
        'unknown_max_s': unk_max_s,
        'recall': _r(m['recall']),
        'safe_fpr': _r(m['safe_fpr']),
        'precision': _r(m['precision']),
        'f1': _r(m['f1']),
        'events': f"{m['event_detected']}/{m['event_total']}",
        'latency_s': '' if m['event_latency_mean_s'] is None else round(float(m['event_latency_mean_s']), 3),
    }


def eval_pixel_rotation_tta(
    name: str,
    model,
    frames: list[np.ndarray],
    labels: list[bool],
    exit_events: list[dict],
    fps: float,
    roi,
    *,
    angles: tuple[int, ...],
    combine: str,
    confidence: float,
    outside_fraction: float,
    device: str,
) -> dict:
    """Run YOLO seg on multiple CCW angles, inverse-rotate masks, and combine."""
    state = BedExitState(window_size=5, threshold=3)
    frame_rows: list[dict] = []
    t0 = time.perf_counter()

    for idx, frame in enumerate(frames):
        h, w = frame.shape[:2]
        roi_mask = roi.mask((h, w))
        rot_masks: list[np.ndarray] = []

        for ang in angles:
            r_frame = rotate_frame_ccw(frame, ang)
            res = model.predict(
                source=r_frame, classes=[0], conf=confidence, imgsz=640,
                device=device, retina_masks=True, verbose=False,
            )[0]
            if res.masks is None or len(res.masks.data) == 0:
                continue
            masks_np = res.masks.data.cpu().numpy()
            # Pick candidate with highest overlap with Bed ROI after inverse rotation
            best_m = None
            best_overlap = -1
            for m_raw in masks_np:
                m_bin = _inv_rotate_mask((m_raw > 0.5).astype(np.uint8), ang) > 0
                ov = int(np.count_nonzero(m_bin & roi_mask))
                if ov > best_overlap:
                    best_overlap = ov
                    best_m = m_bin
            if best_m is not None:
                rot_masks.append(best_m)

        if not rot_masks:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        if combine == 'union':
            comb = np.logical_or.reduce(rot_masks)
        else:  # majority / mean >= 0.5
            comb = (np.mean(rot_masks, axis=0) >= 0.5)

        area = int(np.count_nonzero(comb))
        if area == 0:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        score = float(np.count_nonzero(comb & ~roi_mask) / area)
        out_bool = bool(state.update(score > outside_fraction))
        frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': True, 'output_bool': out_bool})

    return _summarize(name, frame_rows, exit_events, fps, time.perf_counter() - t0)


def eval_tracking(
    name: str,
    model,
    frames: list[np.ndarray],
    labels: list[bool],
    exit_events: list[dict],
    fps: float,
    roi,
    *,
    confidence: float,
    outside_fraction: float,
    device: str,
) -> dict:
    """Lock onto the primary patient ByteTrack ID inside Bed ROI across frames."""
    state = BedExitState(window_size=5, threshold=3)
    frame_rows: list[dict] = []
    primary_id: int | None = None
    t0 = time.perf_counter()

    for idx, frame in enumerate(frames):
        h, w = frame.shape[:2]
        roi_mask = roi.mask((h, w))
        res = model.track(
            source=frame, classes=[0], conf=confidence, imgsz=640,
            device=device, retina_masks=True, persist=True, tracker='bytetrack.yaml', verbose=False,
        )[0]
        if res.masks is None or res.boxes is None or len(res.masks.data) == 0:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        masks_np = res.masks.data.cpu().numpy()
        ids = None if res.boxes.id is None else res.boxes.id.int().cpu().tolist()

        chosen_idx = None
        if primary_id is not None and ids is not None and primary_id in ids:
            chosen_idx = ids.index(primary_id)
        else:
            best_ov = -1
            for i, m_raw in enumerate(masks_np):
                ov = int(np.count_nonzero((m_raw > 0.5) & roi_mask))
                if ov > best_ov:
                    best_ov = ov
                    chosen_idx = i
            if chosen_idx is not None and ids is not None and best_ov > 0:
                primary_id = ids[chosen_idx]

        if chosen_idx is None:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        m_bin = masks_np[chosen_idx] > 0.5
        area = int(np.count_nonzero(m_bin))
        if area == 0:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        score = float(np.count_nonzero(m_bin & ~roi_mask) / area)
        out_bool = bool(state.update(score > outside_fraction))
        frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': True, 'output_bool': out_bool})

    return _summarize(name, frame_rows, exit_events, fps, time.perf_counter() - t0)


def eval_optical_flow_gate(
    name: str,
    weights: str,
    model,
    frames: list[np.ndarray],
    labels: list[bool],
    exit_events: list[dict],
    fps: float,
    roi,
    *,
    confidence: float,
    outside_fraction: float,
    min_flow_px: float,
    device: str,
) -> dict:
    """Gate raw EXIT decisions with Farneback optical flow magnitude inside outside-ROI mask."""
    det = SegmentationROI(
        roi, weights=weights, outside_fraction=outside_fraction,
        device=device, confidence=confidence, model=model,
    )
    state = BedExitState(window_size=5, threshold=3)
    frame_rows: list[dict] = []
    prev_gray = None
    t0 = time.perf_counter()

    for idx, frame in enumerate(frames):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        flow_mag = None
        if prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(
                prev_gray, gray, None, 0.5, 2, 15, 2, 5, 1.2, 0,
            )
            flow_mag = np.hypot(flow[..., 0], flow[..., 1])
        prev_gray = gray

        try:
            raw_bool = bool(det.judge(frame))
        except JudgmentUnavailable:
            state.reset()
            frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': False, 'output_bool': False})
            continue

        if raw_bool and flow_mag is not None and det.last.mask is not None:
            out_region = (det.last.mask > det.mask_threshold) & ~roi.mask(frame.shape)
            if np.any(out_region):
                mean_flow = float(np.mean(flow_mag[out_region]))
                if mean_flow < min_flow_px:
                    raw_bool = False

        out_bool = bool(state.update(raw_bool))
        frame_rows.append({'frame_index': idx, 'expected_bool': bool(labels[idx]), 'valid': True, 'output_bool': out_bool})

    return _summarize(name, frame_rows, exit_events, fps, time.perf_counter() - t0)


def main():
    parser = argparse.ArgumentParser(description='Evaluate Notion 1.1 proposals.')
    parser.add_argument('--weights-n', default='src/part5_bed_exit/models/yolo11n-seg.pt')
    parser.add_argument('--weights-m', default='src/part5_bed_exit/models/yolo11m-seg.pt')
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/run_data/test_input_sequence1_methods_v1.json')
    parser.add_argument('--timeline', default='src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--confidence', type=float, default=0.5)
    parser.add_argument('--outside-fraction', type=float, default=0.05)
    parser.add_argument('--min-flow-px', type=float, default=0.8)
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

    from ultralytics import YOLO
    w_n = str(Path(args.weights_n).resolve())
    w_m = str(Path(args.weights_m).resolve())
    m_n = YOLO(w_n, task='segment')
    m_m = YOLO(w_m, task='segment')

    common = dict(confidence=args.confidence, outside_fraction=args.outside_fraction, device=args.device)
    rows = [
        eval_pixel_rotation_tta('N_0deg_base', m_n, frames, labels, exit_events, fps, roi, angles=(0,), combine='union', **common),
        eval_pixel_rotation_tta('M_0deg_base', m_m, frames, labels, exit_events, fps, roi, angles=(0,), combine='union', **common),
        eval_pixel_rotation_tta('N_rotTTA_0_90_union', m_n, frames, labels, exit_events, fps, roi, angles=(0, 90), combine='union', **common),
        eval_pixel_rotation_tta('N_rotTTA_4way_maj', m_n, frames, labels, exit_events, fps, roi, angles=(0, 90, 180, 270), combine='majority', **common),
        eval_tracking('N_ByteTrack', YOLO(w_n, task='segment'), frames, labels, exit_events, fps, roi, **common),
        eval_optical_flow_gate('N_FlowGate', w_n, m_n, frames, labels, exit_events, fps, roi, min_flow_px=args.min_flow_px, **common),
    ]

    for r in rows:
        print(f"[{r['method']:20s}] fps={r['fps']:5.1f} cov={r['coverage']:.3f} "
              f"unk={r['unknown_frames']:3d}(ep={r['unknown_episodes']},max={r['unknown_max_s']}s) "
              f"rec={r['recall']:.3f} fpr={r['safe_fpr']:.3f} f1={r['f1']:.3f} ev={r['events']}", flush=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = Path(args.output_dir).resolve() if args.output_dir else (
        Path(__file__).resolve().parent / 'runs' / 'eval' / f'proposals_1_1_{stamp}'
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / 'proposals_1_1_results.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f'\n[SAVED] {csv_path}')


if __name__ == '__main__':
    main()
