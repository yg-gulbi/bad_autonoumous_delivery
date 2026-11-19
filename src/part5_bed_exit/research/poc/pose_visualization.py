"""정적 Pose POC의 관절·ROI 판정 근거를 사람이 검수할 수 있게 그리는 모듈.

Pose 후보, 선택된 환자, 관절 신뢰도와 ROI 안팎 상태를 레코드와 이미지로 만들고
여러 결과를 비교 시트로 배치한다. Pose 추론 모델 자체는 구현하지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict

import cv2
import numpy as np

from part5_bed_exit.methods.bed_exit_core import (
    COCO_COMMON,
    COMMON_LANDMARKS,
    MP_COMMON,
    Candidate,
    Diagnostics,
    PolygonROI,
)


COCO_CONNECTIONS = (
    (0, 1), (0, 2), (1, 3), (2, 4),
    (0, 5), (0, 6), (5, 6),
    (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
)

MEDIAPIPE_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 7),
    (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10), (0, 9), (0, 10),
    (0, 11), (0, 12), (11, 12),
    (11, 13), (13, 15), (15, 17), (17, 19), (19, 21), (15, 21),
    (12, 14), (14, 16), (16, 18), (18, 20), (20, 22), (16, 22),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
)

METHOD_METADATA = {
    "yolo_pose": {
        "connections": COCO_CONNECTIONS,
        "common": COCO_COMMON,
        "common_names": {coco: name for name, coco, _ in COMMON_LANDMARKS},
    },
    "mediapipe_pose": {
        "connections": MEDIAPIPE_CONNECTIONS,
        "common": MP_COMMON,
        "common_names": {mediapipe: name for name, _, mediapipe in COMMON_LANDMARKS},
    },
}


def _is_selected(candidate: Candidate, selected_bbox) -> bool:
    """후보 BBox가 환자 선택기가 고른 BBox와 같은지 허용 오차로 비교한다."""
    return selected_bbox is not None and np.allclose(
        candidate.bbox, np.asarray(selected_bbox), rtol=0, atol=1e-3
    )


def _point_payload(
    index: int,
    point,
    common_names: dict[int, str],
    roi: PolygonROI,
    shape,
    point_conf: float,
    tolerance_px: float,
) -> dict:
    """관절 하나의 좌표·신뢰도·ROI 안팎 상태를 JSON용 사전으로 만든다."""
    values = np.asarray(point, dtype=float)
    finite = bool(values.shape == (3,) and np.isfinite(values).all())
    confidence = float(values[2]) if finite else None
    usable = bool(finite and confidence >= point_conf)
    common = index in common_names
    outside_roi = None
    if common and usable:
        outside_roi = not roi.contains(values[:2], shape, tolerance_px)
    return {
        "index": index,
        "name": common_names.get(index),
        "x": float(values[0]) if finite else None,
        "y": float(values[1]) if finite else None,
        "confidence": confidence,
        "usable": usable,
        "common": common,
        "outside_roi": outside_roi,
    }


def build_pose_record(
    *,
    method_name: str,
    model_name: str,
    split: str,
    image_id: str,
    roi: PolygonROI,
    shape,
    candidates: list[Candidate],
    diagnostics: Diagnostics,
    predicted: bool | None,
    inference_time_ms: float,
    failure_reason: str,
    point_conf: float,
    tolerance_px: float,
) -> dict:
    """Serialize every detected pose, including rejected patient candidates."""
    metadata = METHOD_METADATA[method_name]
    serialized = []
    for candidate in candidates:
        points = [] if candidate.points is None else [
            _point_payload(
                index,
                point,
                metadata["common_names"],
                roi,
                shape,
                point_conf,
                tolerance_px,
            )
            for index, point in enumerate(candidate.points)
        ]
        serialized.append({
            "selected_patient": _is_selected(candidate, diagnostics.bbox),
            "bbox_xyxy": [float(value) for value in candidate.bbox],
            "candidate_confidence": float(candidate.confidence),
            "keypoints": points,
        })
    return {
        "method": method_name,
        "model": model_name,
        "split": split,
        "image_id": image_id,
        "frame_width": int(shape[1]),
        "frame_height": int(shape[0]),
        "roi_normalized": roi.vertices.tolist(),
        "point_confidence_threshold": point_conf,
        "roi_tolerance_px": tolerance_px,
        "predicted_bool": predicted,
        "diagnostics": asdict(diagnostics),
        "failure_reason": failure_reason,
        "inference_time_ms": round(float(inference_time_ms), 3),
        "candidate_count": len(candidates),
        "candidates": serialized,
    }


def _draw_pose(
    canvas,
    candidate: Candidate,
    *,
    selected: bool,
    roi: PolygonROI,
    method_name: str,
    point_conf: float,
    tolerance_px: float,
) -> None:
    """후보 한 명의 관절 연결선·점·BBox를 캔버스에 그린다."""
    box = np.rint(candidate.bbox).astype(int)
    box_color = (255, 210, 40) if selected else (125, 125, 125)
    cv2.rectangle(canvas, tuple(box[:2]), tuple(box[2:]), box_color, 2 if selected else 1)
    cv2.putText(
        canvas,
        f"pose {candidate.confidence:.2f}",
        (box[0], max(18, box[1] - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        .45,
        box_color,
        1,
        cv2.LINE_AA,
    )
    if candidate.points is None:
        return
    points = np.asarray(candidate.points, dtype=float)
    usable = np.isfinite(points).all(axis=1) & (points[:, 2] >= point_conf)
    metadata = METHOD_METADATA[method_name]
    for start, end in metadata["connections"]:
        if start < len(points) and end < len(points) and usable[start] and usable[end]:
            color = (210, 210, 210) if selected else (95, 95, 95)
            cv2.line(
                canvas,
                tuple(np.rint(points[start, :2]).astype(int)),
                tuple(np.rint(points[end, :2]).astype(int)),
                color,
                2 if selected else 1,
                cv2.LINE_AA,
            )
    common = set(metadata["common"])
    for index, point in enumerate(points):
        if not usable[index]:
            continue
        center = tuple(np.rint(point[:2]).astype(int))
        if not selected:
            color, radius = (110, 110, 110), 2
        elif index not in common:
            color, radius = (255, 170, 60), 2
        elif roi.contains(point[:2], canvas.shape, tolerance_px):
            color, radius = (60, 220, 80), 4
        else:
            color, radius = (40, 40, 240), 5
        cv2.circle(canvas, center, radius, color, -1, cv2.LINE_AA)


def render_pose_observation(
    frame,
    *,
    method_name: str,
    model_name: str,
    roi: PolygonROI,
    candidates: list[Candidate],
    diagnostics: Diagnostics,
    predicted: bool | None,
    inference_time_ms: float,
    failure_reason: str,
    point_conf: float,
    tolerance_px: float,
) -> np.ndarray:
    """Draw ROI, all pose candidates, selected patient, and decision evidence."""
    canvas = frame.copy()
    roi_pixels = np.rint(roi.pixels(frame.shape)).astype(np.int32)
    overlay = canvas.copy()
    roi_color = (255, 0, 255)
    cv2.fillPoly(overlay, [roi_pixels], roi_color)
    canvas = cv2.addWeighted(overlay, .08, canvas, .92, 0)
    roi_thickness = max(3, round(min(frame.shape[:2]) / 180))
    cv2.polylines(
        canvas, [roi_pixels], True, roi_color, roi_thickness, cv2.LINE_AA
    )
    label_x = int(np.clip(roi_pixels[:, 0].min() + 6, 6, canvas.shape[1] - 100))
    label_y = int(np.clip(roi_pixels[:, 1].min() + 22, 92, canvas.shape[0] - 12))
    cv2.rectangle(
        canvas,
        (label_x - 4, label_y - 18),
        (label_x + 88, label_y + 5),
        roi_color,
        -1,
    )
    cv2.putText(
        canvas,
        "BED ROI",
        (label_x, label_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        .52,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    for candidate in candidates:
        _draw_pose(
            canvas,
            candidate,
            selected=_is_selected(candidate, diagnostics.bbox),
            roi=roi,
            method_name=method_name,
            point_conf=point_conf,
            tolerance_px=tolerance_px,
        )

    status = "UNAVAILABLE" if predicted is None else "EXIT TRUE" if predicted else "SAFE FALSE"
    status_color = (40, 210, 255) if predicted is None else (40, 40, 240) if predicted else (60, 220, 80)
    header = canvas.copy()
    cv2.rectangle(header, (0, 0), (canvas.shape[1], 76), (18, 18, 18), -1)
    canvas = cv2.addWeighted(header, .82, canvas, .18, 0)
    cv2.putText(
        canvas,
        f"{method_name} | {model_name}",
        (12, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        .58,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"{status} | {diagnostics.reason} | {inference_time_ms:.1f} ms",
        (12, 51),
        cv2.FONT_HERSHEY_SIMPLEX,
        .54,
        status_color,
        2,
        cv2.LINE_AA,
    )
    if failure_reason and failure_reason != diagnostics.reason:
        cv2.putText(
            canvas,
            failure_reason[:72],
            (12, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            .38,
            (210, 210, 210),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        canvas,
        "magenta=bed ROI | green=in | red=out | cyan=other | gray=unselected",
        (10, canvas.shape[0] - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        .32,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return canvas


def render_comparison_sheet(items, tile_width: int = 768, columns: int = 2) -> np.ndarray:
    """Create a readable contact sheet from per-image side-by-side comparisons."""
    tiles = []
    for index, (image_id, image) in enumerate(items, 1):
        scale = tile_width / image.shape[1]
        resized = cv2.resize(
            image,
            (tile_width, round(image.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
        header = np.full((34, tile_width, 3), 24, dtype=np.uint8)
        cv2.putText(
            header,
            f"{index:02d} {image_id.split('.rf.')[0]}",
            (10, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            .55,
            (245, 245, 245),
            1,
            cv2.LINE_AA,
        )
        tiles.append(np.vstack([header, resized]))
    if not tiles:
        raise ValueError("comparison sheet requires at least one image")
    blank = np.zeros_like(tiles[0])
    rows = []
    for start in range(0, len(tiles), columns):
        row = tiles[start:start + columns]
        rows.append(np.hstack(row + [blank] * (columns - len(row))))
    return np.vstack(rows)


def write_image(path, image) -> None:
    """부모 디렉터리를 만들고 OpenCV 이미지를 저장하며 실패를 검사한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Cannot write pose visualization: {path}")
