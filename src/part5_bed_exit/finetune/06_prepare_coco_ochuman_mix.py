#!/usr/bin/env python3
"""Build a 1:1 mixed YOLO-seg dataset (COCO 2017 person + OCHuman occluded person)."""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np


def _coco_poly_to_yolo_line(seg_list: list[list[float]], width: int, height: int) -> str | None:
    """Convert one COCO person segmentation (possibly multi-part) into a single YOLO-seg line."""
    parts: list[np.ndarray] = []
    for poly in seg_list:
        if not isinstance(poly, list) or len(poly) < 6:
            continue
        pts = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
        parts.append(pts)
    if not parts:
        return None

    if len(parts) > 1:
        from ultralytics.data.converter import merge_multi_segment
        merged = merge_multi_segment(parts)
        pts = np.concatenate([np.asarray(m, dtype=np.float32).reshape(-1, 2) for m in merged], axis=0)
    else:
        pts = parts[0]

    if len(pts) < 3:
        return None
    xs = np.clip(pts[:, 0] / float(width), 0.0, 1.0)
    ys = np.clip(pts[:, 1] / float(height), 0.0, 1.0)
    coords: list[str] = []
    for x, y in zip(xs, ys):
        coords.append(f'{float(x):.6f}')
        coords.append(f'{float(y):.6f}')
    return '0 ' + ' '.join(coords)


def _link_split(src_img_dir: Path, src_lbl_dir: Path, dst_img_dir: Path, dst_lbl_dir: Path, prefix: str) -> dict:
    dst_img_dir.mkdir(parents=True, exist_ok=True)
    dst_lbl_dir.mkdir(parents=True, exist_ok=True)
    imgs = 0
    insts = 0
    for img_path in sorted(src_img_dir.iterdir()):
        lbl_path = src_lbl_dir / f'{img_path.stem}.txt'
        if not lbl_path.is_file():
            continue
        lines = [ln for ln in lbl_path.read_text(encoding='utf-8').splitlines() if ln.strip()]
        if not lines:
            continue
        dst_img = dst_img_dir / f'{prefix}{img_path.name}'
        dst_lbl = dst_lbl_dir / f'{prefix}{img_path.stem}.txt'
        for p in (dst_img, dst_lbl):
            if p.exists() or p.is_symlink():
                p.unlink()
        os.symlink(img_path.resolve(), dst_img)
        os.symlink(lbl_path.resolve(), dst_lbl)
        imgs += 1
        insts += len(lines)
    return {'images': imgs, 'instances': insts}


def main():
    parser = argparse.ArgumentParser(description='Mix COCO person + OCHuman YOLO-seg dataset.')
    parser.add_argument(
        '--coco-images-dir',
        default='src/part5_bed_exit/research/data/datasets/coco2017/val2017',
    )
    parser.add_argument(
        '--coco-json',
        default='src/part5_bed_exit/research/data/datasets/coco2017/annotations/instances_val2017.json',
    )
    parser.add_argument(
        '--ochuman-seg-dir',
        default='src/part5_bed_exit/finetune/data/ochuman_seg',
    )
    parser.add_argument(
        '--out-dir',
        default='src/part5_bed_exit/finetune/data/coco_ochuman_mix',
    )
    parser.add_argument('--coco-val-count', type=int, default=293)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    coco_img_dir = Path(args.coco_images_dir).resolve()
    coco_json_path = Path(args.coco_json).resolve()
    ochuman_dir = Path(args.ochuman_seg_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    with coco_json_path.open('r', encoding='utf-8') as f:
        coco = json.load(f)

    person_cat_ids = {c['id'] for c in coco['categories'] if c.get('name') == 'person'}
    anns_by_img: dict[int, list[dict]] = {}
    for ann in coco['annotations']:
        if ann.get('category_id') not in person_cat_ids:
            continue
        if ann.get('iscrowd', 0) == 1:
            continue
        seg = ann.get('segmentation')
        if not isinstance(seg, list) or not seg:
            continue
        anns_by_img.setdefault(int(ann['image_id']), []).append(ann)

    valid_imgs = []
    for img_info in coco['images']:
        img_id = int(img_info['id'])
        if img_id not in anns_by_img:
            continue
        src_img = coco_img_dir / img_info['file_name']
        if not src_img.is_file():
            continue
        lines = []
        for ann in anns_by_img[img_id]:
            ln = _coco_poly_to_yolo_line(ann['segmentation'], int(img_info['width']), int(img_info['height']))
            if ln is not None:
                lines.append(ln)
        if lines:
            valid_imgs.append((src_img, lines))

    rng = random.Random(args.seed)
    rng.shuffle(valid_imgs)
    val_n = min(args.coco_val_count, len(valid_imgs) // 5)
    coco_splits = {
        'val': valid_imgs[:val_n],
        'train': valid_imgs[val_n:],
    }

    coco_stats = {}
    for split, items in coco_splits.items():
        img_out = out_dir / 'images' / split
        lbl_out = out_dir / 'labels' / split
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)
        insts = 0
        for src_img, lines in items:
            dst_img = img_out / f'coco_{src_img.name}'
            if dst_img.exists() or dst_img.is_symlink():
                dst_img.unlink()
            os.symlink(src_img.resolve(), dst_img)
            (lbl_out / f'coco_{src_img.stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
            insts += len(lines)
        coco_stats[split] = {'images': len(items), 'instances': insts}

    ochuman_stats = {
        split: _link_split(
            ochuman_dir / 'images' / split,
            ochuman_dir / 'labels' / split,
            out_dir / 'images' / split,
            out_dir / 'labels' / split,
            prefix='ochuman_',
        )
        for split in ('train', 'val')
    }

    yaml_text = (
        f"path: {out_dir}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person\n"
    )
    (out_dir / 'dataset.yaml').write_text(yaml_text, encoding='utf-8')
    print(json.dumps({
        'coco': coco_stats,
        'ochuman': ochuman_stats,
        'yaml': str(out_dir / 'dataset.yaml'),
    }, indent=2))


if __name__ == '__main__':
    main()
