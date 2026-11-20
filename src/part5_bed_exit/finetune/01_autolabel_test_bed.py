#!/usr/bin/env python3
"""colab_auto_label_sequence.ipynb 로직을 로컬 GPU에서 실행해 test_bed.mp4 전체를
YOLO11x-seg (0°/180° 회전 보정)로 자동 세그먼테이션 라벨링하고 파인튜닝 데이터셋을 구축한다.

산출물:
1. ``<out_dir>/raw_labeled/``: 노트북과 동일한 형식의 ``images/``, ``labels_seg/``, ``frame_labels.csv``
2. ``<out_dir>/dataset_0deg/``: 원본 방향 Train/Val 분할 (timeline phase 구간 단위 분리) + ``dataset.yaml``
3. ``<out_dir>/dataset_rot4/``: Train에 0/90/180/270° 4방향 회전을 추가한 데이터셋 + ``dataset.yaml``
4. ``<out_dir>/vis_samples/``: 검수용 폴리곤 마스크 오버레이 30장
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import random
import shutil

import cv2
import numpy as np


VALID_ROTATIONS = (0, 90, 180, 270)
# timeline phase 단위로 train/val을 분리해 인접 프레임 중복 누수를 줄인다 (SAFE 2구간 + EXIT 2구간을 val 배치)
DEFAULT_VAL_PHASES = {
    'supine_return_2',
    'all_limbs_exit',
    'prone_inside',
    'prone_arms_exit',
}


def rotate_image(image: np.ndarray, angle_ccw: int) -> np.ndarray:
    if angle_ccw == 0:
        return image
    if angle_ccw == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if angle_ccw == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle_ccw == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    raise ValueError(f'Unsupported angle: {angle_ccw}')


def rotate_norm_points_ccw(points: np.ndarray, angle_ccw: int) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if pts.size == 0:
        return pts.reshape(0, 2)
    x, y = pts[:, 0], pts[:, 1]
    if angle_ccw == 0:
        out = np.stack([x, y], axis=1)
    elif angle_ccw == 90:
        out = np.stack([y, 1.0 - x], axis=1)
    elif angle_ccw == 180:
        out = np.stack([1.0 - x, 1.0 - y], axis=1)
    elif angle_ccw == 270:
        out = np.stack([1.0 - y, x], axis=1)
    else:
        raise ValueError(f'Unsupported angle: {angle_ccw}')
    return np.clip(out, 0.0, 1.0)


def format_seg_lines(polygons: list[np.ndarray]) -> str:
    lines = []
    for poly in polygons:
        if len(poly) >= 3:
            coords = ' '.join(f'{pt[0]:.6f} {pt[1]:.6f}' for pt in poly)
            lines.append(f'0 {coords}')
    return '\n'.join(lines) + ('\n' if lines else '')


def lookup_timeline(events: list[dict], frame_idx: int) -> tuple[str, str]:
    for ev in events:
        if ev['start_frame'] <= frame_idx < ev['end_frame']:
            hint = 'EXIT' if ev['exit_hint'] is True else 'SAFE'
            return ev['phase'], hint
    return 'unknown', 'UNKNOWN'


def pick_best_seg(model, frame: np.ndarray, device: str, conf: float):
    """노트북의 0°/180° 회전 보정 로직으로 최고 confidence 세그먼테이션 결과를 선택한다."""
    frame_180 = cv2.rotate(frame, cv2.ROTATE_180)
    best_score = 0.0
    best_rot180 = False
    best_res = None
    for rotated, img in ((False, frame), (True, frame_180)):
        res = model.predict(
            source=img,
            classes=[0],
            conf=conf,
            imgsz=640,
            device=device,
            retina_masks=True,
            verbose=False,
        )[0]
        if (res.boxes is not None and len(res.boxes) > 0
                and res.masks is not None and res.masks.xyn is not None):
            score = float(res.boxes.conf.max().cpu().item())
            if score > best_score:
                best_score = score
                best_rot180 = rotated
                best_res = res

    polygons: list[np.ndarray] = []
    if best_res is not None:
        for poly in best_res.masks.xyn:
            if len(poly) >= 3:
                pts = (1.0 - poly) if best_rot180 else poly
                polygons.append(np.clip(np.asarray(pts, dtype=np.float64), 0.0, 1.0))
    return polygons, best_score, best_rot180


def draw_overlay(image: np.ndarray, polygons: list[np.ndarray], caption: str) -> np.ndarray:
    canvas = image.copy()
    h, w = canvas.shape[:2]
    overlay = canvas.copy()
    for poly in polygons:
        pts = np.rint(poly * np.array([w - 1, h - 1])).astype(np.int32)
        cv2.fillPoly(overlay, [pts], (0, 220, 0))
        cv2.polylines(canvas, [pts], True, (0, 255, 255), 2)
    cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0.0, dst=canvas)
    cv2.rectangle(canvas, (0, 0), (w, 28), (20, 20, 20), -1)
    cv2.putText(
        canvas, caption[:80], (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
        0.48, (255, 255, 255), 1, cv2.LINE_AA,
    )
    return canvas


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Run colab_auto_label_sequence YOLO11x-seg (0/180 deg) on test_bed.mp4 and build training datasets.',
    )
    parser.add_argument('--video', default='/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part5_bed_exit/run_data/test_bed.mp4')
    parser.add_argument('--timeline', default='/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part5_bed_exit/run_data/test_input_sequence1_timeline_v1.json')
    parser.add_argument('--out-dir', default='/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part5_bed_exit/finetune/data/test_bed_seg')
    parser.add_argument('--teacher-weights', default='yolo11x-seg.pt')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--conf', type=float, default=0.5)
    parser.add_argument('--step', type=int, default=1)
    parser.add_argument('--force', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    video_path = Path(args.video).expanduser().resolve()
    timeline_path = Path(args.timeline).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            raise FileExistsError(f'Output directory not empty (use --force): {out_dir}')
        shutil.rmtree(out_dir)

    raw_dir = out_dir / 'raw_labeled'
    ds0_dir = out_dir / 'dataset_0deg'
    ds4_dir = out_dir / 'dataset_rot4'
    vis_dir = out_dir / 'vis_samples'

    for d in (
        raw_dir / 'images',
        raw_dir / 'labels_seg',
        ds0_dir / 'images' / 'train',
        ds0_dir / 'images' / 'val',
        ds0_dir / 'labels' / 'train',
        ds0_dir / 'labels' / 'val',
        ds4_dir / 'images' / 'train',
        ds4_dir / 'images' / 'val',
        ds4_dir / 'labels' / 'train',
        ds4_dir / 'labels' / 'val',
        vis_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    timeline_events = json.loads(timeline_path.read_text(encoding='utf-8'))['events']

    from ultralytics import YOLO
    teacher_path = Path(args.teacher_weights).expanduser()
    if not teacher_path.is_file() and '/' not in args.teacher_weights:
        cache_dir = Path(__file__).resolve().parent / 'data' / 'pretrained'
        cache_dir.mkdir(parents=True, exist_ok=True)
        teacher_target = str(cache_dir / args.teacher_weights)
    else:
        teacher_target = str(teacher_path)
    seg_yolo = YOLO(teacher_target, task='segment')

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {video_path}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    stats = Counter()
    csv_rows = []
    vis_pool = []
    frame_idx = 0

    print(f'[AUTOLABEL START] video={video_path.name} frames={total} teacher={teacher_target} device={args.device}', flush=True)
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if frame_idx % args.step != 0:
                frame_idx += 1
                continue

            stem = f'frame_{frame_idx:04d}'
            img_name = f'{stem}.jpg'
            phase, tl_hint = lookup_timeline(timeline_events, frame_idx)
            polygons, best_conf, rot180 = pick_best_seg(seg_yolo, frame, args.device, args.conf)

            # 1) 원본 노트북과 동일한 raw_labeled 저장
            cv2.imwrite(str(raw_dir / 'images' / img_name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            label_text = format_seg_lines(polygons)
            (raw_dir / 'labels_seg' / f'{stem}.txt').write_text(label_text, encoding='utf-8')

            stats['total_frames'] += 1
            if rot180:
                stats['selected_180deg'] += 1

            csv_rows.append({
                'frame_idx': frame_idx,
                'image': img_name,
                'timestamp_sec': f'{frame_idx / fps:.3f}',
                'timeline_phase': phase,
                'timeline_exit_hint': tl_hint,
                'seg_detected': bool(polygons),
                'seg_conf': f'{best_conf:.4f}',
                'seg_rotated_180': rot180,
                'num_polygons': len(polygons),
            })

            if not polygons:
                stats['dropped_no_mask'] += 1
                frame_idx += 1
                continue

            split = 'val' if phase in DEFAULT_VAL_PHASES else 'train'
            stats[f'valid_{split}'] += 1

            # 2) dataset_0deg 저장
            cv2.imwrite(str(ds0_dir / 'images' / split / img_name), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (ds0_dir / 'labels' / split / f'{stem}.txt').write_text(label_text, encoding='utf-8')

            # 3) dataset_rot4 저장 (train에 0/90/180/270° 4방향 생성, val은 원본 0°)
            rots = VALID_ROTATIONS if split == 'train' else (0,)
            for rot in rots:
                rot_img = rotate_image(frame, rot)
                rot_polys = [rotate_norm_points_ccw(p, rot) for p in polygons]
                rstem = f'{stem}_r{rot}'
                cv2.imwrite(
                    str(ds4_dir / 'images' / split / f'{rstem}.jpg'),
                    rot_img,
                    [cv2.IMWRITE_JPEG_QUALITY, 92],
                )
                (ds4_dir / 'labels' / split / f'{rstem}.txt').write_text(
                    format_seg_lines(rot_polys), encoding='utf-8'
                )
                stats[f'rot4_{split}'] += 1

            if frame_idx % 20 == 0:
                vis_pool.append((frame.copy(), polygons, f'f={frame_idx} {phase} conf={best_conf:.2f} r180={rot180}'))

            if (frame_idx + 1) % 256 == 0 or (frame_idx + 1) == total:
                print(
                    f'[PROGRESS] {frame_idx + 1}/{total} '
                    f'train={stats["valid_train"]} val={stats["valid_val"]} '
                    f'rot180={stats["selected_180deg"]} dropped={stats["dropped_no_mask"]}',
                    flush=True,
                )
            frame_idx += 1
    finally:
        cap.release()

    with (raw_dir / 'frame_labels.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)

    for ds in (ds0_dir, ds4_dir):
        (ds / 'dataset.yaml').write_text(
            f'path: {ds}\ntrain: images/train\nval: images/val\nnames:\n  0: person\n',
            encoding='utf-8',
        )

    rng = random.Random(42)
    rng.shuffle(vis_pool)
    for i, (img, polys, cap_txt) in enumerate(vis_pool[:30]):
        cv2.imwrite(str(vis_dir / f'{i:02d}.jpg'), draw_overlay(img, polys, cap_txt))

    summary = {
        'video': str(video_path),
        'teacher_weights': teacher_target,
        'val_phases': sorted(DEFAULT_VAL_PHASES),
        'stats': dict(stats),
        'dataset_0deg_yaml': str(ds0_dir / 'dataset.yaml'),
        'dataset_rot4_yaml': str(ds4_dir / 'dataset.yaml'),
    }
    (out_dir / 'autolabel_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(f'[AUTOLABEL DONE] {json.dumps(dict(stats), ensure_ascii=False)}', flush=True)


if __name__ == '__main__':
    main()
