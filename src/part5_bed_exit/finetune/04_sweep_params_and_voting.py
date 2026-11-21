#!/usr/bin/env python3
"""Step 4: 재학습 없이 추론 임계치(conf, mask_threshold, outside_fraction)와 다수결 투표(K/N)를 스윕한다.

모델별 추론(``candidates()``)은 ``yolo_confidence`` 당 1회만 실행하고,
``mask_threshold`` / ``outside_fraction`` / ``voting (K/N)`` 조합을 공식 지표로 일괄 비교한다.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import time

import cv2
import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import SegmentationROI
from part5_bed_exit.pretrained import read_config
from research.integration_benchmark.metrics import load_timeline, selection_metrics


def load_frames(video_path: Path) -> tuple[list[tuple[int, np.ndarray]], float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {video_path}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frames = []
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frames.append((idx, frame))
            idx += 1
    finally:
        cap.release()
    return frames, fps


def extract_scores_for_thresholds(
    weights: Path,
    config_path: Path,
    frames: list[tuple[int, np.ndarray]],
    *,
    device: str,
    yolo_conf: float,
    mask_thresholds: tuple[float, ...],
) -> tuple[dict[float, list[float | None]], float]:
    """한 번의 모델 추론으로 각 ``mask_threshold``별 프레임 단위 ``outside_fraction`` 점수를 구한다."""
    _, roi, _ = read_config(config_path)
    detector = SegmentationROI(roi, weights, device=device, confidence=yolo_conf)
    scores_by_mt: dict[float, list[float | None]] = {mt: [] for mt in mask_thresholds}
    roi_masks: dict[tuple[int, int], np.ndarray] = {}

    started = time.perf_counter()
    try:
        for _, frame in frames:
            shape = frame.shape[:2]
            if shape not in roi_masks:
                roi_masks[shape] = roi.mask(frame.shape)
            roi_mask = roi_masks[shape]

            cands = detector.candidates(frame)
            patient, _reason = detector.selector.select(cands, frame.shape)
            if patient is None or patient.mask is None:
                for mt in mask_thresholds:
                    scores_by_mt[mt].append(None)
                continue

            for mt in mask_thresholds:
                m = patient.mask > mt
                area = int(m.sum())
                if not area:
                    scores_by_mt[mt].append(None)
                else:
                    scores_by_mt[mt].append(float(np.count_nonzero(m & ~roi_mask) / area))
    finally:
        detector.close()

    elapsed_s = time.perf_counter() - started
    fps = len(frames) / elapsed_s if elapsed_s > 0 else 0.0
    return scores_by_mt, round(fps, 1)


def evaluate_scores(
    scores: list[float | None],
    labels: list[bool],
    events: list[dict],
    fps_video: float,
    *,
    outside_fraction: float,
    voting_window: int,
    voting_threshold: int,
) -> dict:
    state = BedExitState(window_size=voting_window, threshold=voting_threshold)
    rows = []
    for idx, (score, gt) in enumerate(zip(scores, labels)):
        if score is None:
            state.reset()
            rows.append({'frame_index': idx, 'valid': False, 'output_bool': None, 'expected_bool': gt})
            continue
        raw = score > outside_fraction
        out = state.update(raw)
        rows.append({'frame_index': idx, 'valid': True, 'output_bool': out, 'expected_bool': gt})

    return selection_metrics(rows, events, fps_video)


def main():
    parser = argparse.ArgumentParser(description='Sweep confidence, mask/outside thresholds, and voting K/N.')
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json')
    parser.add_argument('--timeline', default='src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--output-dir', default='src/part5_bed_exit/finetune/runs/03_param_voting_sweep')
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    config_path = Path(args.config).resolve()
    timeline_path = Path(args.timeline).resolve()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    frames, fps_video = load_frames(video_path)
    labels, events, _ = load_timeline(timeline_path, len(frames))

    candidates = [
        ('B1_yolo11n_seg', Path('src/part5_bed_exit/models/yolo11n-seg.pt').resolve()),
        ('B2_yolo11s_seg', Path('src/part5_bed_exit/models/yolo11s-seg.pt').resolve()),
        ('B3_yolo11m_seg', Path('src/part5_bed_exit/models/yolo11m-seg.pt').resolve()),
        ('FT1_yolo11n_0deg', Path('src/part5_bed_exit/finetune/runs/ft1_yolo11n_seg_testbed_0deg/weights/best.pt').resolve()),
        ('FT2_yolo11n_rot4', Path('src/part5_bed_exit/finetune/runs/ft2_yolo11n_seg_testbed_rot4/weights/best.pt').resolve()),
    ]
    confs = (0.35, 0.50)
    mask_thresholds = (0.50, 0.60)
    outside_fractions = (0.05, 0.08, 0.10, 0.12)
    votings = ((5, 3), (5, 4), (7, 5), (9, 6))

    all_rows = []
    for label, weights in candidates:
        if not weights.is_file():
            print(f'[SKIP] missing {weights}')
            continue
        for conf in confs:
            print(f'[INFER] {label} conf={conf} ...', flush=True)
            scores_by_mt, proc_fps = extract_scores_for_thresholds(
                weights, config_path, frames, device=args.device,
                yolo_conf=conf, mask_thresholds=mask_thresholds,
            )
            for mt in mask_thresholds:
                for of in outside_fractions:
                    for win, thr in votings:
                        m = evaluate_scores(
                            scores_by_mt[mt], labels, events, fps_video,
                            outside_fraction=of, voting_window=win, voting_threshold=thr,
                        )
                        all_rows.append({
                            'model': label,
                            'conf': conf,
                            'mask_thr': mt,
                            'outside_frac': of,
                            'vote': f'{thr}/{win}',
                            'fps': proc_fps,
                            'coverage': m['coverage'],
                            'recall': m['recall'],
                            'safe_fpr': m['safe_fpr'],
                            'precision': m['precision'],
                            'f1': m['f1'],
                            'events': f"{m['event_detected']}/{m['event_total']}",
                            'event_detected': m['event_detected'],
                            'event_total': m['event_total'],
                            'latency_s': m['event_latency_mean_s'],
                        })

    csv_path = out_dir / 'sweep_results.csv'
    fieldnames = list(all_rows[0].keys())
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    (out_dir / 'sweep_results.json').write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )

    # 1) 모든 이벤트(9/9) 검출 유지 + F1 내림차순 Top 12
    full_ev = [r for r in all_rows if r['event_detected'] == r['event_total']]
    top_f1 = sorted(full_ev, key=lambda r: (-r['f1'], r['safe_fpr'], -r['coverage']))[:12]
    print('\n=== TOP 12 (Events 9/9, highest F1) ===')
    print('| Model | conf | mask | out | vote | FPS | Cov | Recall | SAFE_FPR | Prec | F1 | Lat(s) |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in top_f1:
        print(
            f"| {r['model']} | {r['conf']} | {r['mask_thr']} | {r['outside_frac']} | "
            f"{r['vote']} | {r['fps']} | {r['coverage']:.3f} | {r['recall']:.3f} | "
            f"{r['safe_fpr']:.3f} | {r['precision']:.3f} | {r['f1']:.3f} | {r['latency_s']:.3f} |"
        )

    # 2) Pretrained 모델(B1/B2/B3)만 별도 Top 6
    pre_full = [r for r in full_ev if r['model'].startswith('B')]
    top_pre = sorted(pre_full, key=lambda r: (-r['f1'], r['safe_fpr'], -r['coverage']))[:6]
    print('\n=== TOP 6 PRETRAINED ONLY (Events 9/9) ===')
    for r in top_pre:
        print(
            f"| {r['model']} | {r['conf']} | {r['mask_thr']} | {r['outside_frac']} | "
            f"{r['vote']} | {r['fps']} | {r['coverage']:.3f} | {r['recall']:.3f} | "
            f"{r['safe_fpr']:.3f} | {r['precision']:.3f} | {r['f1']:.3f} | {r['latency_s']:.3f} |"
        )
    print(f'\n[SAVED] {csv_path}')


if __name__ == '__main__':
    main()
