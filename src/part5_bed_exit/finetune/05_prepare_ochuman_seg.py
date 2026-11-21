#!/usr/bin/env python3
"""Convert OCHuman COCO-RLE annotations into a single-class YOLO segmentation dataset."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np


def decode_coco_rle(rle: dict) -> np.ndarray:
    """Decode COCO compressed RLE dict {'size': [h, w], 'counts': str} to uint8 mask (H, W)."""
    h, w = int(rle['size'][0]), int(rle['size'][1])
    s = rle['counts']
    cnts: list[int] = []
    p = 0
    m = 0
    n = len(s)
    while p < n:
        x = 0
        k = 0
        more = 1
        while more:
            c = ord(s[p]) - 48
            x |= (c & 0x1f) << (5 * k)
            more = c & 0x20
            p += 1
            k += 1
            if not more and (c & 0x10):
                x |= -1 << (5 * k)
        if m > 2:
            x += cnts[m - 2]
        cnts.append(x)
        m += 1
    mask = np.zeros(h * w, dtype=np.uint8)
    pos = 0
    val = 0
    for c in cnts:
        if val == 1:
            mask[pos:pos + c] = 1
        pos += c
        val ^= 1
    return mask.reshape((w, h)).T


def mask_to_yolo_polygons(mask: np.ndarray, width: int, height: int, min_area: float = 25.0) -> list[list[float]]:
    """Extract normalized YOLO polygon segments from a binary mask.

    When occlusion splits one person into multiple disconnected pieces, merge them
    into one continuous polygon using ultralytics.data.converter.merge_multi_segment
    so a single occluded person remains one instance.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    valid_segments: list[np.ndarray] = []
    for cnt in contours:
        if len(cnt) < 3:
            continue
        if cv2.contourArea(cnt) < min_area:
            continue
        pts = cnt.reshape(-1, 2).astype(np.float32)
        if len(pts) > 200:
            eps = 0.002 * cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, eps, True)
            if len(approx) >= 3:
                pts = approx.reshape(-1, 2).astype(np.float32)
        valid_segments.append(pts)

    if not valid_segments:
        return []

    if len(valid_segments) > 1:
        from ultralytics.data.converter import merge_multi_segment
        merged = merge_multi_segment([s.reshape(-1, 2) for s in valid_segments])
        joined = np.concatenate([np.asarray(m, dtype=np.float32).reshape(-1, 2) for m in merged], axis=0)
        polys = [joined] if len(joined) >= 3 else []
    else:
        polys = valid_segments

    out: list[list[float]] = []
    for poly in polys:
        xs = np.clip(poly[:, 0] / float(width), 0.0, 1.0)
        ys = np.clip(poly[:, 1] / float(height), 0.0, 1.0)
        coords: list[float] = []
        for x, y in zip(xs, ys):
            coords.append(round(float(x), 6))
            coords.append(round(float(y), 6))
        if len(coords) >= 6:
            out.append(coords)
    return out


def convert_split(
    json_path: Path,
    images_src_dir: Path,
    out_img_dir: Path,
    out_lbl_dir: Path,
    max_images: int | None = None,
) -> dict:
    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_lbl_dir.mkdir(parents=True, exist_ok=True)

    with json_path.open('r', encoding='utf-8') as f:
        coco = json.load(f)

    anns_by_img: dict[int, list[dict]] = {}
    for ann in coco['annotations']:
        if ann.get('iscrowd', 0) == 1:
            continue
        if not ann.get('segmentation'):
            continue
        anns_by_img.setdefault(int(ann['image_id']), []).append(ann)

    images = coco['images']
    if max_images is not None:
        images = images[:max_images]

    written_imgs = 0
    written_anns = 0
    skipped_empty = 0

    for img_info in images:
        img_id = int(img_info['id'])
        file_name = img_info['file_name']
        src_img = images_src_dir / file_name
        if not src_img.is_file():
            continue

        width = int(img_info['width'])
        height = int(img_info['height'])
        lines: list[str] = []
        for ann in anns_by_img.get(img_id, []):
            seg = ann['segmentation']
            if isinstance(seg, dict):
                mask = decode_coco_rle(seg)
                polys = mask_to_yolo_polygons(mask, width, height)
            elif isinstance(seg, list):
                polys = []
                for p in seg:
                    if len(p) >= 6:
                        coords = [round(float(v) / (width if i % 2 == 0 else height), 6) for i, v in enumerate(p)]
                        polys.append(coords)
            else:
                polys = []

            for coords in polys:
                lines.append('0 ' + ' '.join(f'{v:.6f}' for v in coords))
                written_anns += 1

        if not lines:
            skipped_empty += 1
            continue

        dst_img = out_img_dir / file_name
        if dst_img.exists() or dst_img.is_symlink():
            dst_img.unlink()
        os.symlink(src_img.resolve(), dst_img)

        stem = Path(file_name).stem
        (out_lbl_dir / f'{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        written_imgs += 1

    return {
        'images': written_imgs,
        'instances': written_anns,
        'skipped_empty': skipped_empty,
    }


def main():
    parser = argparse.ArgumentParser(description='Prepare OCHuman YOLO-seg dataset.')
    parser.add_argument(
        '--ochuman-dir',
        default='src/part5_bed_exit/research/data/datasets/ochuman/ochuman',
        help='Directory containing images/ and ochuman_coco_format_*.json',
    )
    parser.add_argument(
        '--out-dir',
        default='src/part5_bed_exit/finetune/data/ochuman_seg',
        help='Output YOLO-seg dataset directory',
    )
    parser.add_argument('--val-limit', type=int, default=500, help='Max val images from test split')
    args = parser.parse_args()

    src_dir = Path(args.ochuman_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    images_src = src_dir / 'images'

    train_stats = convert_split(
        src_dir / 'ochuman_coco_format_val_range_0.00_1.00.json',
        images_src,
        out_dir / 'images' / 'train',
        out_dir / 'labels' / 'train',
    )
    val_stats = convert_split(
        src_dir / 'ochuman_coco_format_test_range_0.00_1.00.json',
        images_src,
        out_dir / 'images' / 'val',
        out_dir / 'labels' / 'val',
        max_images=args.val_limit,
    )

    yaml_text = (
        f"path: {out_dir}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person\n"
    )
    (out_dir / 'dataset.yaml').write_text(yaml_text, encoding='utf-8')
    print(json.dumps({'train': train_stats, 'val': val_stats, 'yaml': str(out_dir / 'dataset.yaml')}, indent=2))


if __name__ == '__main__':
    main()
