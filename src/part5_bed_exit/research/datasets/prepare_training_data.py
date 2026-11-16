#!/usr/bin/env python3
"""Prepare non-destructive YOLO training datasets from the acquired sources."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path


ROBOFLOW_CLASS_MAP = {0: 0, 1: 1, 2: 0}
ROBOFLOW_NAMES = {0: "person", 1: "hospital_bed"}

# Source order from the Mannequin README is LSP-style. Head top and neck do
# not have exact COCO equivalents, so only the 12 directly matching limbs are
# copied. COCO face points remain absent (0, 0, visibility=0).
LSP_TO_COCO = {
    0: 16,  # right ankle
    1: 14,  # right knee
    2: 12,  # right hip
    3: 11,  # left hip
    4: 13,  # left knee
    5: 15,  # left ankle
    6: 10,  # right wrist
    7: 8,   # right elbow
    8: 6,   # right shoulder
    9: 5,   # left shoulder
    10: 7,  # left elbow
    11: 9,  # left wrist
}


def convert_roboflow_label_lines(lines: list[str]) -> list[str]:
    """Merge bed_exit/in_bed boxes into person while retaining bed boxes."""
    converted = []
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        source_class = int(fields[0])
        if source_class not in ROBOFLOW_CLASS_MAP or len(fields) != 5:
            raise ValueError(f"Unexpected Roboflow detection label: {line!r}")
        converted.append(" ".join([str(ROBOFLOW_CLASS_MAP[source_class]), *fields[1:]]))
    return converted


def lsp_frame_to_yolo_pose(joints, width: int, height: int, padding: float = 0.10) -> str:
    """Convert one 3x16 LSP-style frame to a COCO17-compatible YOLO row."""
    keypoints = [[0.0, 0.0, 0] for _ in range(17)]
    labeled_xy = []
    for lsp_index, coco_index in LSP_TO_COCO.items():
        x = float(joints[0, lsp_index])
        y = float(joints[1, lsp_index])
        invisible = bool(joints[2, lsp_index])
        if x <= 0 or y <= 0 or x > width or y > height:
            continue
        visibility = 1 if invisible else 2
        keypoints[coco_index] = [x / width, y / height, visibility]
        labeled_xy.append((x, y))
    if len(labeled_xy) < 4:
        raise ValueError("At least four mapped limb keypoints are required")

    xs, ys = zip(*labeled_xy)
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    pad_x = max((x2 - x1) * padding, 1.0)
    pad_y = max((y2 - y1) * padding, 1.0)
    x1, x2 = max(0.0, x1 - pad_x), min(float(width), x2 + pad_x)
    y1, y2 = max(0.0, y1 - pad_y), min(float(height), y2 + pad_y)
    box = [
        ((x1 + x2) / 2) / width,
        ((y1 + y2) / 2) / height,
        (x2 - x1) / width,
        (y2 - y1) / height,
    ]
    values = [0, *box, *(value for point in keypoints for value in point)]
    return " ".join(str(value) if isinstance(value, int) else f"{value:.8f}" for value in values)


def _ensure_new_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)


def _copy_pair(image: Path, label_text: str, output: Path, split: str) -> None:
    image_dir = output / "images" / split
    label_dir = output / "labels" / split
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image, image_dir / image.name)
    (label_dir / f"{image.stem}.txt").write_text(label_text.rstrip() + "\n", encoding="utf-8")


def prepare_roboflow_detection(source: Path, output: Path) -> dict:
    """Create a two-class person/bed detection dataset without changing source."""
    _ensure_new_output(output)
    counts = Counter()
    split_names = {"train": "train", "valid": "val", "test": "test"}
    for source_split, target_split in split_names.items():
        image_dir = source / source_split / "images"
        label_dir = source / source_split / "labels"
        for image in sorted(image_dir.glob("*")):
            if not image.is_file():
                continue
            label = label_dir / f"{image.stem}.txt"
            converted = convert_roboflow_label_lines(label.read_text(encoding="utf-8").splitlines())
            _copy_pair(image, "\n".join(converted), output, target_split)
            counts[f"images_{target_split}"] += 1
            for row in converted:
                counts[f"class_{row.split()[0]}"] += 1

    yaml = (
        f"path: {output.resolve()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        "names:\n  0: person\n  1: hospital_bed\n"
    )
    (output / "data.yaml").write_text(yaml, encoding="utf-8")
    manifest = {
        "task": "detection",
        "source": str(source.resolve()),
        "class_mapping": {"bed_exit": "person", "in_bed": "person", "hospital_bed": "hospital_bed"},
        "counts": dict(sorted(counts.items())),
        "limitations": [
            "bed_exit and in_bed state semantics are intentionally removed",
            "source images are one 512x512 camera sequence and do not establish generalization",
            "blanket conditions are not labeled",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _mannequin_split(index: int) -> str:
    if index <= 250:
        return "train"
    if index <= 281:
        return "val"
    return "test"


def prepare_mannequin_pose(source: Path, output: Path) -> dict:
    """Create a COCO17-compatible YOLO pose dataset from Mannequin LSP labels."""
    import cv2
    from scipy.io import loadmat

    _ensure_new_output(output)
    images = sorted((source / "images").glob("*.jpg"))
    joints = loadmat(source / "joints_gt.mat")["joints_gt"]
    if joints.shape != (3, 16, len(images)):
        raise ValueError(f"Unexpected joints_gt shape: {joints.shape}; images={len(images)}")

    counts = Counter()
    for offset, image in enumerate(images):
        index = offset + 1
        split = _mannequin_split(index)
        frame = cv2.imread(str(image))
        if frame is None:
            raise ValueError(f"Unreadable image: {image}")
        height, width = frame.shape[:2]
        label = lsp_frame_to_yolo_pose(joints[:, :, offset], width, height)
        _copy_pair(image, label, output, split)
        counts[f"images_{split}"] += 1

    flip_idx = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
    yaml = (
        f"path: {output.resolve()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        "kpt_shape: [17, 3]\n"
        f"flip_idx: {flip_idx}\n"
        "names:\n  0: person\n"
    )
    (output / "data.yaml").write_text(yaml, encoding="utf-8")
    manifest = {
        "task": "pose",
        "source": str(source.resolve()),
        "keypoint_shape": [17, 3],
        "mapped_keypoints": 12,
        "absent_keypoints": ["nose", "left_eye", "right_eye", "left_ear", "right_ear"],
        "ignored_source_keypoints": ["neck", "head_top", "dummy_15", "dummy_16"],
        "split": "provisional contiguous 250 train / 31 val / 31 test",
        "counts": dict(sorted(counts.items())),
        "limitations": [
            "all images are mannequin portrait-view frames without blankets",
            "the source provides no participant or sequence grouping metadata",
            "the provisional split must not be reported as patient-level generalization",
            "boxes are derived from mapped limb keypoints with 10 percent padding",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=("roboflow-detection", "mannequin-pose"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.task == "roboflow-detection":
        result = prepare_roboflow_detection(args.source, args.output)
    else:
        result = prepare_mannequin_pose(args.source, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
