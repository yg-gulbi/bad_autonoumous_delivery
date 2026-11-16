"""Evaluate all 9 Part 5 bed-exit methods on Roboflow Bed Exit v4 (hospital_bed ROI + Yes/No GT)."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np
from PIL import Image
import torch
from transformers import AutoImageProcessor, AutoModelForPoseEstimation, ViTMAEForPreTraining
from ultralytics import YOLO

PKG_ROOT = Path(__file__).resolve().parents[2]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from part5_bed_exit.methods.bed_exit_core import (
    COCO_COMMON,
    MP_COMMON,
    MediaPipePoseROI,
    PolygonROI,
    YoloPoseROI,
    pose_decision,
)
from part5_bed_exit.methods.detection_method import DetectionBedExitMethod
from part5_bed_exit.methods.segmentation_method import SegmentationBedExitMethod

DEFAULT_ROBOFLOW_ROOT = PKG_ROOT / 'research' / 'data' / 'datasets' / 'roboflow_bed_exit_v4' / 'raw'
DEFAULT_OUT_DIR = PKG_ROOT / 'results' / 'roboflow106_compare'

COCO_EDGES = (
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
)


def yolo_box_to_polygon(cx: float, cy: float, bw: float, bh: float) -> list[list[float]]:
    x1 = float(np.clip(cx - bw / 2.0, 0.0, 1.0))
    y1 = float(np.clip(cy - bh / 2.0, 0.0, 1.0))
    x2 = float(np.clip(cx + bw / 2.0, 0.0, 1.0))
    y2 = float(np.clip(cy + bh / 2.0, 0.0, 1.0))
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def collect_samples(root: Path, splits: list[str]) -> list[dict]:
    samples = []
    for split in splits:
        img_dir = root / split / 'images'
        lbl_dir = root / split / 'labels'
        if not lbl_dir.is_dir():
            continue
        img_by_stem = {p.stem: p for p in img_dir.iterdir() if p.is_file()}
        for lbl_path in sorted(lbl_dir.glob('*.txt')):
            img_path = img_by_stem.get(lbl_path.stem)
            if img_path is None:
                continue
            beds = []
            has_exit = False
            has_in_bed = False
            for line in lbl_path.read_text(encoding='utf-8').splitlines():
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                cls_id = int(parts[0])
                cx, cy, bw, bh = (float(v) for v in parts[1:5])
                if cls_id == 1:
                    beds.append((bw * bh, cx, cy, bw, bh))
                elif cls_id == 0:
                    has_exit = True
                elif cls_id == 2:
                    has_in_bed = True
            if not beds or (not has_exit and not has_in_bed):
                continue
            beds.sort(key=lambda x: x[0], reverse=True)
            _, cx, cy, bw, bh = beds[0]
            samples.append({
                'split': split,
                'image_path': img_path,
                'gt': 'EXIT' if has_exit else 'SAFE',
                'roi_pts': yolo_box_to_polygon(cx, cy, bw, bh),
            })
    return samples


def vitpose_infer(processor, model, pil_img: Image.Image, box_xyxy: np.ndarray, device: str) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in box_xyxy)
    boxes = [[[x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)]]]
    inputs = processor(pil_img, boxes=boxes, return_tensors='pt').to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    pose_results = processor.post_process_pose_estimation(outputs, boxes=boxes)
    if not pose_results or not pose_results[0]:
        return np.zeros((0, 3), dtype=np.float32)
    res = pose_results[0][0]
    kps = res['keypoints'].cpu().numpy()
    scores = res['scores'].cpu().numpy()
    return np.concatenate([kps, scores[:, None]], axis=1).astype(np.float32)


def mae_reconstruct_lower_bed(
    mae_proc, mae_model, frame_bgr: np.ndarray, roi_poly: np.ndarray, device: str
) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    inputs = mae_proc(images=Image.fromarray(rgb), return_tensors='pt').to(device)
    with torch.no_grad():
        outputs = mae_model(**inputs)
    logits = outputs.logits
    patch_size = mae_model.config.patch_size
    num_h = num_w = int(mae_model.config.image_size // patch_size)
    x = logits.reshape(1, num_h, num_w, patch_size, patch_size, 3)
    x = torch.einsum('nhwpqc->nchpwq', x).reshape(1, 3, num_h * patch_size, num_w * patch_size)
    mean = torch.tensor(mae_proc.image_mean, device=device).view(1, 3, 1, 1)
    std = torch.tensor(mae_proc.image_std, device=device).view(1, 3, 1, 1)
    rec = ((x * std + mean).clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)
    rec_full = cv2.resize(rec, (w, h), interpolation=cv2.INTER_LINEAR)

    roi_px = np.rint(roi_poly * np.array([w - 1, h - 1], dtype=np.float32)).astype(np.int32)
    roi_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(roi_mask, [roi_px], 1)
    y_min, y_max = int(roi_px[:, 1].min()), int(roi_px[:, 1].max())
    y_mid = (y_min + y_max) // 2
    lower_mask = (roi_mask > 0) & (np.arange(h)[:, None] >= y_mid)

    out_rgb = rgb.copy()
    out_rgb[lower_mask] = rec_full[lower_mask]
    return cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)


def select_bed_person_box(yolo_model, frame_bgr: np.ndarray, roi: PolygonROI, device: str):
    res = yolo_model.predict(
        source=frame_bgr, classes=[0], conf=0.25, imgsz=640, device=device, verbose=False
    )[0]
    if res.boxes is None or len(res.boxes) == 0:
        return None
    boxes = res.boxes.xyxy.cpu().numpy()
    best = None
    best_overlap = -1.0
    for b in boxes:
        overlap = 1.0 - roi.box_outside(b, frame_bgr.shape)
        if overlap >= 0.2 and overlap > best_overlap:
            best_overlap = overlap
            best = b
    return best


def eval_points(pts: np.ndarray, roi: PolygonROI, shape, policy: str):
    decision, _, reason = pose_decision(
        pts, roi, shape, COCO_COMMON, (5, 6, 11, 12),
        point_conf=0.5, tolerance_px=3.0, policy=policy,
    )
    if decision is None:
        return 'UNAVAIL', reason
    return ('EXIT' if decision else 'SAFE'), reason


def draw_pose_canvas(
    frame: np.ndarray,
    roi: PolygonROI,
    title: str,
    verdict: str,
    reason: str,
    pts: np.ndarray | None,
    common_idx: tuple[int, ...],
    edges: tuple[tuple[int, int], ...],
    bbox=None,
    point_conf: float = 0.5,
) -> np.ndarray:
    canvas = frame.copy()
    h, w = canvas.shape[:2]
    poly = np.rint(roi.pixels((h, w))).astype(np.int32)
    cv2.polylines(canvas, [poly], True, (255, 255, 0), 2)

    color = (0, 200, 0) if verdict == 'SAFE' else ((0, 0, 255) if verdict == 'EXIT' else (0, 165, 255))
    if bbox is not None:
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

    if pts is not None and len(pts) > max(common_idx):
        for a, b in edges:
            if a < len(pts) and b < len(pts) and pts[a, 2] >= point_conf and pts[b, 2] >= point_conf:
                p1 = (int(round(pts[a, 0])), int(round(pts[a, 1])))
                p2 = (int(round(pts[b, 0])), int(round(pts[b, 1])))
                cv2.line(canvas, p1, p2, (200, 200, 200), 2)
        for idx in common_idx:
            if pts[idx, 2] >= point_conf:
                px, py = float(pts[idx, 0]), float(pts[idx, 1])
                inside = roi.contains((px, py), (h, w), 3.0)
                c = (0, 220, 0) if inside else (0, 0, 255)
                cv2.circle(canvas, (int(round(px)), int(round(py))), 4, c, -1)

    cv2.rectangle(canvas, (0, 0), (w, 28), (20, 20, 20), -1)
    cv2.putText(canvas, f'{title} | {verdict} ({reason})', (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 2)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description='Compare all 9 Part 5 methods on Roboflow Bed Exit v4.')
    parser.add_argument('--data-root', type=Path, default=DEFAULT_ROBOFLOW_ROOT)
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument('--splits', nargs='+', default=['train', 'valid', 'test'])
    parser.add_argument('--policy', choices=['any_body', 'torso'], default='any_body')
    parser.add_argument('--save-panels', action='store_true', help='Save 2x5 comparison grids and contact sheets.')
    args = parser.parse_args()

    samples = collect_samples(args.data_root, args.splits)
    if not samples:
        raise RuntimeError(f'No samples with hospital_bed + bed_exit/in_bed found under {args.data_root}')
    print(f'Collected {len(samples)} samples (EXIT={sum(s["gt"]=="EXIT" for s in samples)}, SAFE={sum(s["gt"]=="SAFE" for s in samples)})')

    args.out_dir.mkdir(parents=True, exist_ok=True)
    panels_dir = args.out_dir / 'panels'
    if args.save_panels:
        panels_dir.mkdir(parents=True, exist_ok=True)

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'Device: {device}, Pose policy: {args.policy}')

    det = DetectionBedExitMethod(roi_config=samples[0]['roi_pts'], device=device)
    seg = SegmentationBedExitMethod(roi_config=samples[0]['roi_pts'], device=device)
    yolo_det = YOLO(str(PKG_ROOT / 'models' / 'yolo11n.pt'))

    vp_simple_proc = AutoImageProcessor.from_pretrained('usyd-community/vitpose-base-simple')
    vp_simple_mod = AutoModelForPoseEstimation.from_pretrained('usyd-community/vitpose-base-simple').to(device).eval()
    vp_base_proc = AutoImageProcessor.from_pretrained('usyd-community/vitpose-base')
    vp_base_mod = AutoModelForPoseEstimation.from_pretrained('usyd-community/vitpose-base').to(device).eval()
    mae_proc = AutoImageProcessor.from_pretrained('facebook/vit-mae-base')
    mae_mod = ViTMAEForPreTraining.from_pretrained('facebook/vit-mae-base').to(device).eval()

    method_names = [
        'detection', 'segmentation', 'yolo_pose', 'mediapipe_pose',
        'yolo_vitpose_simple', 'yolo_vitpose_base', 'fullroi_vitpose_base',
        'mae_yolo_vitpose_base', 'mae_fullroi_vitpose_base',
    ]
    stats = {
        m: {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0, 'unavail': 0, 'ms': [], 'reasons': {}}
        for m in method_names
    }

    rows = []
    thumbs = []

    def record(name: str, gt: str, verdict: str, reason: str, dt_ms: float):
        s = stats[name]
        s['ms'].append(dt_ms)
        s['reasons'][reason] = s['reasons'].get(reason, 0) + 1
        if verdict == 'UNAVAIL':
            s['unavail'] += 1
        elif gt == 'EXIT' and verdict == 'EXIT':
            s['tp'] += 1
        elif gt == 'SAFE' and verdict == 'EXIT':
            s['fp'] += 1
        elif gt == 'SAFE' and verdict == 'SAFE':
            s['tn'] += 1
        elif gt == 'EXIT' and verdict == 'SAFE':
            s['fn'] += 1

    for sample in samples:
        img_path = sample['image_path']
        gt = sample['gt']
        roi_pts = sample['roi_pts']
        roi = PolygonROI(roi_pts)
        frame = cv2.imread(str(img_path))
        h, w = frame.shape[:2]
        roi_px = roi.pixels((h, w))
        full_roi_box = np.array([roi_px[:, 0].min(), roi_px[:, 1].min(), roi_px[:, 0].max(), roi_px[:, 1].max()], dtype=np.float32)
        pil_orig = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        row = {'split': sample['split'], 'image': img_path.name, 'gt': gt}

        # 1. Detection
        det.reset(roi_config=roi_pts)
        t0 = time.perf_counter()
        try:
            v, r = ('EXIT' if det.judge(frame) else 'SAFE'), det.last_result.reason
        except Exception as exc:
            v, r = 'UNAVAIL', str(exc)
        record('detection', gt, v, r, (time.perf_counter() - t0) * 1000.0)
        row['detection'] = v

        # 2. Segmentation
        seg.reset(roi_config=roi_pts)
        t0 = time.perf_counter()
        try:
            v, r = ('EXIT' if seg.judge(frame) else 'SAFE'), seg.last_result.reason
        except Exception as exc:
            v, r = 'UNAVAIL', str(exc)
        record('segmentation', gt, v, r, (time.perf_counter() - t0) * 1000.0)
        row['segmentation'] = v

        # 3. YOLO Pose
        yolo_pose = YoloPoseROI(roi=roi, weights=PKG_ROOT / 'models' / 'yolo11n-pose.pt', device=device, policy=args.policy)
        t0 = time.perf_counter()
        try:
            v, r = ('EXIT' if yolo_pose.judge(frame) else 'SAFE'), yolo_pose.last.reason
        except Exception as exc:
            v, r = 'UNAVAIL', str(exc)
        record('yolo_pose', gt, v, r, (time.perf_counter() - t0) * 1000.0)
        row['yolo_pose'] = v
        pts_yp = np.asarray(yolo_pose.last.points, np.float32) if yolo_pose.last.points else (
            yolo_pose.last_candidates[0].points if yolo_pose.last_candidates else None
        )

        # 4. MediaPipe Pose
        mp_pose = MediaPipePoseROI(roi=roi, weights=PKG_ROOT / 'models' / 'pose_landmarker_full.task', policy=args.policy)
        t0 = time.perf_counter()
        try:
            v, r = ('EXIT' if mp_pose.judge(frame) else 'SAFE'), mp_pose.last.reason
        except Exception as exc:
            v, r = 'UNAVAIL', str(exc)
        record('mediapipe_pose', gt, v, r, (time.perf_counter() - t0) * 1000.0)
        row['mediapipe_pose'] = v
        pts_mp = np.asarray(mp_pose.last.points, np.float32) if mp_pose.last.points else (
            mp_pose.last_candidates[0].points if mp_pose.last_candidates else None
        )
        mp_pose.close()

        # 5-7. ViTPose variants
        t_box0 = time.perf_counter()
        box = select_bed_person_box(yolo_det, frame, roi, device)
        box_ms = (time.perf_counter() - t_box0) * 1000.0

        vp_results = {}
        for key, proc, mod, use_full in [
            ('yolo_vitpose_simple', vp_simple_proc, vp_simple_mod, False),
            ('yolo_vitpose_base', vp_base_proc, vp_base_mod, False),
            ('fullroi_vitpose_base', vp_base_proc, vp_base_mod, True),
        ]:
            t0 = time.perf_counter()
            b = full_roi_box if use_full else box
            if b is None:
                v, r, pts = 'UNAVAIL', 'no_person', None
                dt = box_ms
            else:
                pts = vitpose_infer(proc, mod, pil_orig, b, device)
                v, r = eval_points(pts, roi, frame.shape, args.policy)
                dt = (0.0 if use_full else box_ms) + (time.perf_counter() - t0) * 1000.0
            record(key, gt, v, r, dt)
            row[key] = v
            vp_results[key] = (v, r, pts, b)

        # 8-9. MAE + ViTPose variants
        t_mae0 = time.perf_counter()
        mae_frame = mae_reconstruct_lower_bed(mae_proc, mae_mod, frame, roi.vertices, device)
        mae_ms = (time.perf_counter() - t_mae0) * 1000.0
        pil_mae = Image.fromarray(cv2.cvtColor(mae_frame, cv2.COLOR_BGR2RGB))
        t_bm0 = time.perf_counter()
        box_m = select_bed_person_box(yolo_det, mae_frame, roi, device)
        bm_ms = (time.perf_counter() - t_bm0) * 1000.0

        for key, use_full in [('mae_yolo_vitpose_base', False), ('mae_fullroi_vitpose_base', True)]:
            t0 = time.perf_counter()
            b = full_roi_box if use_full else box_m
            if b is None:
                v, r, pts = 'UNAVAIL', 'no_person', None
                dt = mae_ms + bm_ms
            else:
                pts = vitpose_infer(vp_base_proc, vp_base_mod, pil_mae, b, device)
                v, r = eval_points(pts, roi, frame.shape, args.policy)
                dt = mae_ms + (0.0 if use_full else bm_ms) + (time.perf_counter() - t0) * 1000.0
            record(key, gt, v, r, dt)
            row[key] = v
            vp_results[key] = (v, r, pts, b)

        rows.append(row)

        if args.save_panels:
            c1 = det.render(frame)
            c2 = seg.render(frame)
            c3 = draw_pose_canvas(frame, roi, 'YOLO-Pose', row['yolo_pose'], yolo_pose.last.reason, pts_yp, COCO_COMMON, COCO_EDGES, yolo_pose.last.bbox)
            c4 = draw_pose_canvas(frame, roi, 'MediaPipe', row['mediapipe_pose'], mp_pose.last.reason, pts_mp, MP_COMMON, (), mp_pose.last.bbox)
            c5 = draw_pose_canvas(frame, roi, 'ViTPose-Simple', *vp_results['yolo_vitpose_simple'][:3], COCO_COMMON, COCO_EDGES, vp_results['yolo_vitpose_simple'][3])
            c6 = draw_pose_canvas(frame, roi, 'ViTPose-Base', *vp_results['yolo_vitpose_base'][:3], COCO_COMMON, COCO_EDGES, vp_results['yolo_vitpose_base'][3])
            c7 = draw_pose_canvas(frame, roi, 'FullROI+ViTPose', *vp_results['fullroi_vitpose_base'][:3], COCO_COMMON, COCO_EDGES, vp_results['fullroi_vitpose_base'][3])
            c8 = draw_pose_canvas(mae_frame, roi, 'MAE+YOLO+VP', *vp_results['mae_yolo_vitpose_base'][:3], COCO_COMMON, COCO_EDGES, vp_results['mae_yolo_vitpose_base'][3])
            c9 = draw_pose_canvas(mae_frame, roi, 'MAE+FullROI+VP', *vp_results['mae_fullroi_vitpose_base'][:3], COCO_COMMON, COCO_EDGES, vp_results['mae_fullroi_vitpose_base'][3])
            c10 = frame.copy()
            cv2.polylines(c10, [np.rint(roi.pixels((h, w))).astype(np.int32)], True, (255, 255, 0), 2)
            cv2.rectangle(c10, (0, 0), (w, 28), (20, 20, 20), -1)
            cv2.putText(c10, f'GT: {gt} ({sample["split"]})', (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 2)

            tiles = [cv2.resize(c, (480, 360)) for c in [c1, c2, c3, c4, c5, c6, c7, c8, c9, c10]]
            grid = cv2.vconcat([cv2.hconcat(tiles[:5]), cv2.hconcat(tiles[5:])])
            cv2.imwrite(str(panels_dir / f'{sample["split"]}_{img_path.stem}.png'), grid)
            thumbs.append(cv2.resize(grid, (0, 0), fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA))

    if args.save_panels and thumbs:
        for idx in range(0, len(thumbs), 10):
            sheet = cv2.vconcat(thumbs[idx : idx + 10])
            cv2.imwrite(str(args.out_dir / f'roboflow_sheet_{idx // 10 + 1}.jpg'), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])

    summary = {}
    for m, s in stats.items():
        valid = s['tp'] + s['fp'] + s['tn'] + s['fn']
        total = valid + s['unavail']
        prec = s['tp'] / max(s['tp'] + s['fp'], 1)
        rec = s['tp'] / max(s['tp'] + s['fn'], 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9) if (s['tp'] > 0) else 0.0
        summary[m] = {
            'total': total,
            'valid': valid,
            'unavail': s['unavail'],
            'tp': s['tp'],
            'fp': s['fp'],
            'tn': s['tn'],
            'fn': s['fn'],
            'valid_acc_pct': round(100.0 * (s['tp'] + s['tn']) / max(valid, 1), 1),
            'overall_acc_pct': round(100.0 * (s['tp'] + s['tn']) / max(total, 1), 1),
            'precision_pct': round(100.0 * prec, 1) if (s['tp'] + s['fp']) else None,
            'recall_pct': round(100.0 * rec, 1) if (s['tp'] + s['fn']) else None,
            'f1_pct': round(100.0 * f1, 1) if valid else None,
            'avg_ms': round(statistics.mean(s['ms']), 2),
            'median_ms': round(statistics.median(s['ms']), 2),
            'reasons': s['reasons'],
        }

    (args.out_dir / 'roboflow106_summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    with (args.out_dir / 'roboflow106_results.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
