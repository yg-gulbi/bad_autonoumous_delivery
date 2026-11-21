#!/usr/bin/env python3
"""Phase 2 dataset builder: COCO 2017 person-only YOLO-seg (0deg or 4-way rot4)."""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import cv2
import numpy as np


def _coco_poly_to_points(seg_list: list[list[float]]) -> np.ndarray | None:
    parts: list[np.ndarray] = []
    for poly in seg_list:
        if not isinstance(poly, list) or len(poly) < 6:
            continue
        parts.append(np.asarray(poly, dtype=np.float32).reshape(-1, 2))
    if not parts:
        return None
    if len(parts) > 1:
        from ultralytics.data.converter import merge_multi_segment
        merged = merge_multi_segment(parts)
        pts = np.concatenate([np.asarray(m, dtype=np.float32).reshape(-1, 2) for m in merged], axis=0)
    else:
        pts = parts[0]
    return pts if len(pts) >= 3 else None


def _rotate_points_ccw(pts: np.ndarray, width: int, height: int, k: int) -> tuple[np.ndarray, int, int]:
    """Rotate pixel coordinates counter-clockwise by k * 90 degrees (k in {0,1,2,3})."""
    k = k % 4
    if k == 0:
        return pts.copy(), width, height
    x, y = pts[:, 0], pts[:, 1]
    if k == 1:  # 90 CCW: new_w=height, new_h=width
        return np.stack([y, width - 1 - x], axis=1), height, width
    if k == 2:  # 180
        return np.stack([width - 1 - x, height - 1 - y], axis=1), width, height
    # k == 3 (270 CCW / 90 CW)
    return np.stack([height - 1 - y, x], axis=1), height, width


def _points_to_yolo_line(pts: np.ndarray, width: int, height: int) -> str:
    xs = np.clip(pts[:, 0] / float(width), 0.0, 1.0)
    ys = np.clip(pts[:, 1] / float(height), 0.0, 1.0)
    coords = []
    for x, y in zip(xs, ys):
        coords.append(f'{float(x):.6f}')
        coords.append(f'{float(y):.6f}')
    return '0 ' + ' '.join(coords)


def main():
    parser = argparse.ArgumentParser(description='Prepare COCO person-only YOLO-seg dataset.')
    parser.add_argument(
        '--coco-images-dir',
        default='src/part5_bed_exit/research/data/datasets/coco2017/val2017',
    )
    parser.add_argument(
        '--coco-json',
        default='src/part5_bed_exit/research/data/datasets/coco2017/annotations/instances_val2017.json',
    )
    parser.add_argument('--out-dir', default='', help='Output dir (auto-named if empty)')
    parser.add_argument('--val-count', type=int, default=293)
    parser.add_argument('--rot4', action='store_true', help='Generate 0/90/180/270 rotated train copies')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    coco_img_dir = Path(args.coco_images_dir).resolve()
    coco_json_path = Path(args.coco_json).resolve()
    default_name = 'coco_person_rot4' if args.rot4 else 'coco_person_only'
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (
        Path('src/part5_bed_exit/finetune/data').resolve() / default_name
    )

    with coco_json_path.open('r', encoding='utf-8') as f:
        coco = json.load(f)

    person_cat_ids = {c['id'] for c in coco['categories'] if c.get('name') == 'person'}
    anns_by_img: dict[int, list[dict]] = {}
    for ann in coco['annotations']:
        if ann.get('category_id') not in person_cat_ids or ann.get('iscrowd', 0) == 1:
            continue
        seg = ann.get('segmentation')
        if isinstance(seg, list) and seg:
            anns_by_img.setdefault(int(ann['image_id']), []).append(ann)

    valid_imgs = []
    for img_info in coco['images']:
        img_id = int(img_info['id'])
        if img_id not in anns_by_img:
            continue
        src_img = coco_img_dir / img_info['file_name']
        if not src_img.is_file():
            continue
        polys = []
        for ann in anns_by_img[img_id]:
            pts = _coco_poly_to_points(ann['segmentation'])
            if pts is not None:
                polys.append(pts)
        if polys:
            valid_imgs.append((src_img, int(img_info['width']), int(img_info['height']), polys))

    rng = random.Random(args.seed)
    rng.shuffle(valid_imgs)
    val_n = min(args.val_count, len(valid_imgs) // 5)
    splits = {'val': valid_imgs[:val_n], 'train': valid_imgs[val_n:]}

    stats = {}
    for split, items in splits.items():
        img_out = out_dir / 'images' / split
        lbl_out = out_dir / 'labels' / split
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        angles = (0, 1, 2, 3) if (args.rot4 and split == 'train') else (0,)
        written_imgs = 0
        written_insts = 0
        for src_img, w, h, polys in items:
            img_bgr = None
            for k in angles:
                suffix = f'_r{k * 90}' if k > 0 else ''
                dst_img = img_out / f'{src_img.stem}{suffix}{src_img.suffix}'
                dst_lbl = lbl_out / f'{src_img.stem}{suffix}.txt'
                if k == 0:
                    if dst_img.exists() or dst_img.is_symlink():
                        dst_img.unlink()
                    os.symlink(src_img.resolve(), dst_img)
                    rw, rh = w, h
                    rpolys = polys
                else:
                    if img_bgr is None:
                        img_bgr = cv2.imread(str(src_img))
                    rot_code = (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE)[k - 1]
                    cv2.imwrite(str(dst_img), cv2.rotate(img_bgr, rot_code))
                    rpolys = []
                    rw, rh = w, h
                    for pts in polys:
                        rpts, rw, rh = _rotate_points_ccw(pts, w, h, k)
                        rpolys.append(rpts)

                lines = [_points_to_yolo_line(p, rw, rh) for p in rpolys]
                dst_lbl.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                written_imgs += 1
                written_insts += len(lines)
        stats[split] = {'images': written_imgs, 'instances': written_insts}

    yaml_text = (
        f'path: {out_dir}\n'
        'train: images/train\n'
        'val: images/val\n'
        'names:\n'
        '  0: person\n'
    )
    (out_dir / 'dataset.yaml').write_text(yaml_text, encoding='utf-8')
    print(json.dumps({'rot4': args.rot4, 'splits': stats, 'yaml': str(out_dir / 'dataset.yaml')}, indent=2))


if __name__ == '__main__':
    main()
