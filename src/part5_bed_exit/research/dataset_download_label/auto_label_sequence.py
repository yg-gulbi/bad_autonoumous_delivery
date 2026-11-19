"""영상 프레임에 여러 AI 모델의 자동 라벨과 ROI 이탈 힌트를 생성하는 연구 도구.

BBox, Segmentation, Pose 결과를 YOLO·COCO 형식으로 저장하고 타임라인 정답과 함께
기록한다. 운영 추론이나 ROS 발행이 아니라 후속 데이터 검수용 초벌 라벨 생성기다.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import torch
from ultralytics import YOLO

PKG_ROOT = Path(__file__).resolve().parents[2]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from part5_bed_exit.methods.bed_exit_core import (
    COCO_COMMON,
    DetectionROI,
    PolygonROI,
    SegmentationROI,
    pose_decision,
)

DEFAULT_VIDEO = PKG_ROOT / 'research' / 'data' / 'videos' / 'test_input_sequence1.mp4'
DEFAULT_ROI = PKG_ROOT / 'research' / 'configs' / 'test_input_sequence1_roi_v1.json'
DEFAULT_TIMELINE = PKG_ROOT / 'research' / 'configs' / 'test_input_sequence1_timeline_v1.json'
DEFAULT_OUT = PKG_ROOT / 'research' / 'data' / 'labeled' / 'test_input_sequence1'


def lookup_timeline(timeline_events: list[dict], frame_idx: int) -> tuple[str, str]:
    """프레임 번호가 속한 타임라인 구간의 상태 라벨과 이탈 힌트를 반환한다."""
    for ev in timeline_events:
        if ev['start_frame'] <= frame_idx < ev['end_frame']:
            hint = 'EXIT' if ev['exit_hint'] is True else ('SAFE' if ev['exit_hint'] is False else 'END')
            return ev['phase'], hint
    return 'unknown', 'UNKNOWN'


def main() -> None:
    """인자를 읽어 영상 전체의 자동 라벨·미리보기·메타데이터를 생성한다."""
    parser = argparse.ArgumentParser(description='Auto-label video frames into YOLO + COCO formats.')
    parser.add_argument('--video', type=Path, default=DEFAULT_VIDEO)
    parser.add_argument('--roi', type=Path, default=DEFAULT_ROI)
    parser.add_argument('--timeline', type=Path, default=DEFAULT_TIMELINE)
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--step', type=int, default=1, help='Sample every N frames (1 = all frames).')
    parser.add_argument('--max-frames', type=int, default=0, help='Optional limit for quick smoke runs.')
    parser.add_argument('--det-weights', type=str, default='yolo11x.pt', help='Large teacher detector weights.')
    parser.add_argument('--seg-weights', type=str, default='yolo11x-seg.pt', help='Large teacher segmentation weights.')
    parser.add_argument('--pose-weights', type=str, default='yolo11x-pose.pt', help='Large teacher pose weights.')
    parser.add_argument('--device', type=str, default='cuda:0' if torch.cuda.is_available() else 'cpu')
    args = parser.parse_args()

    roi_cfg = json.loads(args.roi.read_text(encoding='utf-8'))
    roi_pts = roi_cfg['bed_polygon']
    poly_roi = PolygonROI(roi_pts)
    timeline_events = json.loads(args.timeline.read_text(encoding='utf-8'))['events']

    img_dir = args.out_dir / 'images'
    lbl_bbox_dir = args.out_dir / 'labels_bbox'
    lbl_seg_dir = args.out_dir / 'labels_seg'
    lbl_pose_dir = args.out_dir / 'labels_pose'
    for d in (img_dir, lbl_bbox_dir, lbl_seg_dir, lbl_pose_dir):
        d.mkdir(parents=True, exist_ok=True)

    det_yolo = YOLO(args.det_weights)
    seg_yolo = YOLO(args.seg_weights)
    pose_yolo = YOLO(args.pose_weights)

    det_method = DetectionROI(
        poly_roi, args.det_weights, device=args.device, model=det_yolo, outside_fraction=0.05
    )
    seg_method = SegmentationROI(
        poly_roi, args.seg_weights, device=args.device, model=seg_yolo, outside_fraction=0.05
    )

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {args.video}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)

    coco = {
        'images': [],
        'annotations': [],
        'categories': [{'id': 1, 'name': 'person', 'supercategory': 'person'}],
    }
    csv_rows = []
    ann_id = 1
    frame_idx = 0
    saved = 0

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame_idx % args.step != 0:
            frame_idx += 1
            continue
        if args.max_frames > 0 and saved >= args.max_frames:
            break

        h, w = frame.shape[:2]
        stem = f'frame_{frame_idx:04d}'
        img_name = f'{stem}.jpg'
        cv2.imwrite(str(img_dir / img_name), frame)

        # 1) BBox auto-label
        det_res = det_yolo.predict(source=frame, classes=[0], conf=0.5, imgsz=640, device=args.device, verbose=False)[0]
        bbox_lines = []
        if det_res.boxes is not None and len(det_res.boxes) > 0:
            for xywhn in det_res.boxes.xywhn.cpu().numpy():
                bbox_lines.append(f'0 {xywhn[0]:.6f} {xywhn[1]:.6f} {xywhn[2]:.6f} {xywhn[3]:.6f}')
        (lbl_bbox_dir / f'{stem}.txt').write_text('\n'.join(bbox_lines), encoding='utf-8')

        # 2) Segmentation auto-label
        seg_res = seg_yolo.predict(source=frame, classes=[0], conf=0.5, imgsz=640, device=args.device, verbose=False)[0]
        seg_lines = []
        if seg_res.masks is not None and seg_res.masks.xyn is not None:
            for poly in seg_res.masks.xyn:
                if len(poly) >= 3:
                    coords = ' '.join(f'{pt[0]:.6f} {pt[1]:.6f}' for pt in poly)
                    seg_lines.append(f'0 {coords}')
        (lbl_seg_dir / f'{stem}.txt').write_text('\n'.join(seg_lines), encoding='utf-8')

        # 3) Pose auto-label (YOLO pose + COCO JSON)
        pose_res = pose_yolo.predict(source=frame, classes=[0], conf=0.5, imgsz=640, device=args.device, verbose=False)[0]
        pose_lines = []
        pose_auto_exit = 'UNAVAIL'
        coco['images'].append({'id': frame_idx + 1, 'file_name': img_name, 'width': w, 'height': h})

        if pose_res.boxes is not None and len(pose_res.boxes) > 0 and pose_res.keypoints is not None:
            xywhn_all = pose_res.boxes.xywhn.cpu().numpy()
            xyxy_all = pose_res.boxes.xyxy.cpu().numpy()
            kpts_xy = pose_res.keypoints.xy.cpu().numpy()
            kpts_xyn = pose_res.keypoints.xyn.cpu().numpy()
            kpts_conf = pose_res.keypoints.conf.cpu().numpy()

            for i in range(len(xywhn_all)):
                bx, by, bw, bh = (float(v) for v in xywhn_all[i])
                kp_tokens = []
                coco_kps = []
                for k in range(17):
                    kx, ky = float(kpts_xyn[i, k, 0]), float(kpts_xyn[i, k, 1])
                    kc = float(kpts_conf[i, k])
                    vis = 2 if kc >= 0.5 else (1 if kc > 0.1 else 0)
                    kp_tokens.append(f'{kx:.6f} {ky:.6f} {vis}')
                    coco_kps.extend([float(kpts_xy[i, k, 0]), float(kpts_xy[i, k, 1]), vis])
                pose_lines.append(f'0 {bx:.6f} {by:.6f} {bw:.6f} {bh:.6f} ' + ' '.join(kp_tokens))

                x1, y1, x2, y2 = (float(v) for v in xyxy_all[i])
                coco['annotations'].append({
                    'id': ann_id,
                    'image_id': frame_idx + 1,
                    'category_id': 1,
                    'bbox': [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)],
                    'area': max(0.0, (x2 - x1) * (y2 - y1)),
                    'iscrowd': 0,
                    'num_keypoints': sum(1 for v in coco_kps[2::3] if v > 0),
                    'keypoints': coco_kps,
                })
                ann_id += 1

            pts0 = np.concatenate([kpts_xy[0], kpts_conf[0][:, None]], axis=1)
            dec, _, _ = pose_decision(pts0, poly_roi, frame.shape, COCO_COMMON, (5, 6, 11, 12), point_conf=0.5, tolerance_px=3.0, policy='any_body')
            pose_auto_exit = 'EXIT' if dec is True else ('SAFE' if dec is False else 'UNAVAIL')
        (lbl_pose_dir / f'{stem}.txt').write_text('\n'.join(pose_lines), encoding='utf-8')

        # ROI auto-exit hints from Detection & Segmentation
        try:
            det_auto = 'EXIT' if det_method.judge(frame) else 'SAFE'
        except Exception:
            det_auto = 'UNAVAIL'
        try:
            seg_auto = 'EXIT' if seg_method.judge(frame) else 'SAFE'
        except Exception:
            seg_auto = 'UNAVAIL'

        phase, tl_hint = lookup_timeline(timeline_events, frame_idx)
        csv_rows.append({
            'frame_idx': frame_idx,
            'image': img_name,
            'timestamp_sec': f'{frame_idx / fps:.3f}',
            'timeline_phase': phase,
            'timeline_exit_hint': tl_hint,
            'auto_exit_det': det_auto,
            'auto_exit_seg': seg_auto,
            'auto_exit_pose': pose_auto_exit,
        })

        frame_idx += 1
        saved += 1

    cap.release()
    (args.out_dir / 'annotations_coco.json').write_text(json.dumps(coco, indent=2), encoding='utf-8')
    with (args.out_dir / 'frame_labels.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f'Auto-labeled {saved} frames to {args.out_dir}')


if __name__ == '__main__':
    main()
