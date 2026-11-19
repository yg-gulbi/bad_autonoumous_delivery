#!/usr/bin/env python3
"""고정된 POC 샘플에 Roboflow BBox를 그려 수동 검수 시트를 만드는 CLI.

실험 요약 JSON과 원본 이미지를 읽어 선택 근거를 한 장의 표 형태 이미지로 저장한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


CLASS_COLORS = {
    "bed_exit": (40, 40, 220),
    "hospital_bed": (40, 190, 40),
    "in_bed": (220, 120, 40),
}


def _pixel_box(box: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    """정규화 BBox를 지정 이미지 크기의 정수 픽셀 좌표로 변환한다."""
    x1, y1, x2, y2 = box
    return tuple(round(value) for value in (x1 * width, y1 * height, x2 * width, y2 * height))


def render(summary_path: Path, output_path: Path, tile_size: int = 320) -> None:
    """POC 요약과 원본 이미지에서 라벨이 그려진 검수 시트를 만든다."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    dataset_root = Path(summary["dataset_root"])
    tiles = []
    for index, sample in enumerate(summary["selection"], 1):
        image_path = dataset_root / sample["split"] / "images" / f'{sample["image_id"]}.jpg'
        frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Cannot decode {image_path}")
        height, width = frame.shape[:2]
        roi_box = [sample["roi"][0][0], sample["roi"][0][1],
                   sample["roi"][2][0], sample["roi"][2][1]]
        cv2.rectangle(frame, _pixel_box(roi_box, width, height)[:2],
                      _pixel_box(roi_box, width, height)[2:], CLASS_COLORS["hospital_bed"], 3)
        if sample["patient_bbox"] is not None:
            patient_class = "bed_exit" if sample["category"] == "bed_exit" else "in_bed"
            patient_box = _pixel_box(sample["patient_bbox"], width, height)
            cv2.rectangle(frame, patient_box[:2], patient_box[2:], CLASS_COLORS[patient_class], 3)
        frame = cv2.resize(frame, (tile_size, tile_size), interpolation=cv2.INTER_AREA)
        header = np.zeros((52, tile_size, 3), dtype=np.uint8)
        cv2.putText(header, f"{index:02d} {sample['category']} {sample['split']}",
                    (8, 21), cv2.FONT_HERSHEY_SIMPLEX, .52, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(header, sample["image_id"].split(".rf.")[0],
                    (8, 43), cv2.FONT_HERSHEY_SIMPLEX, .5, (210, 210, 210), 1, cv2.LINE_AA)
        tiles.append(np.vstack((header, frame)))
    columns = 4
    rows = []
    blank = np.zeros_like(tiles[0])
    for start in range(0, len(tiles), columns):
        row = tiles[start:start + columns]
        rows.append(np.hstack(row + [blank] * (columns - len(row))))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), np.vstack(rows)):
        raise OSError(f"Cannot write {output_path}")


def main() -> int:
    """명령행 경로와 타일 크기를 받아 검수 시트 생성을 실행한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.summary, args.output)
    print(f"Review sheet: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
