"""Shared ROI, patient tracking, rendering, and YOLO base utilities for Part 5 methods."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
import json
import os
from pathlib import Path

import cv2
import numpy as np

from part5_bed_exit.bed_exit_method import BedExitMethod

DEFAULT_ROI_PATH = Path(__file__).resolve().parent / 'bed_roi.json'
DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[2] / 'models'
ROI_ENV_VAR = 'BED_EXIT_ROI_CONFIG'


@dataclass
class MethodDiagnostics:
    """Latest single-frame inference metrics and artifacts for visualization."""

    method: str
    unsafe: bool | None = None
    reason: str = 'not_run'
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    center: tuple[float, float] | None = None
    center_inside: bool | None = None
    overlap_ratio: float | None = None
    outside_ratio: float | None = None
    mask: np.ndarray | None = None


class BedROI:
    """Normalized bed polygon ROI with per-resolution rasterized bool mask cache."""

    def __init__(self, polygon):
        pts = np.asarray(polygon, dtype=np.float32)
        if (
            pts.ndim != 2
            or pts.shape[1] != 2
            or len(pts) < 3
            or not np.isfinite(pts).all()
            or np.any((pts < 0.0) | (pts > 1.0))
        ):
            raise ValueError('bed_polygon must contain >= 3 finite [x, y] points in [0, 1]')
        if abs(cv2.contourArea(pts)) < 1e-6:
            raise ValueError('bed_polygon must have non-zero area')
        self.polygon = pts
        self._masks: dict[tuple[int, int], np.ndarray] = {}

    @classmethod
    def from_json(cls, path: str | Path) -> BedROI:
        """Load a BedROI from a JSON file with a 'bed_polygon' field."""
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if 'bed_polygon' not in data:
            raise ValueError(f"Missing 'bed_polygon' key in ROI config: {path}")
        return cls(data['bed_polygon'])

    def polygon_pixels(self, shape: tuple[int, int]) -> np.ndarray:
        """Convert normalized ROI vertices to pixel coordinates for (H, W)."""
        h, w = int(shape[0]), int(shape[1])
        return self.polygon * np.array([w - 1, h - 1], dtype=np.float32)

    def mask(self, shape: tuple[int, int]) -> np.ndarray:
        """Return cached (H, W) bool mask where True marks inside the safe bed ROI."""
        h, w = int(shape[0]), int(shape[1])
        if h <= 0 or w <= 0:
            raise ValueError('Image shape dimensions must be positive')
        key = (h, w)
        if key not in self._masks:
            canvas = np.zeros(key, dtype=np.uint8)
            poly_px = np.rint(self.polygon_pixels(key)).astype(np.int32)
            cv2.fillPoly(canvas, [poly_px], 1)
            self._masks[key] = canvas.astype(bool)
        return self._masks[key]

    def contains(self, point: tuple[float, float], shape: tuple[int, int], tolerance_px: float = 0.0) -> bool:
        """Return True when (x, y) pixel point lies inside or on the ROI boundary."""
        poly_px = self.polygon_pixels(shape)
        return bool(cv2.pointPolygonTest(poly_px, (float(point[0]), float(point[1])), True) >= -tolerance_px)

    def box_outside_fraction(self, box: np.ndarray, shape: tuple[int, int]) -> float:
        """Return fraction of an xyxy bounding box lying outside the bed ROI."""
        h, w = shape[:2]
        x1, y1 = np.floor(box[:2]).astype(int)
        x2, y2 = np.ceil(box[2:]).astype(int)
        x1, x2 = int(np.clip(x1, 0, w)), int(np.clip(x2, 0, w))
        y1, y2 = int(np.clip(y1, 0, h)), int(np.clip(y2, 0, h))
        if x2 <= x1 or y2 <= y1:
            raise ValueError('Empty or out-of-bounds bounding box')
        roi_slice = self.mask((h, w))[y1:y2, x1:x2]
        return float(1.0 - roi_slice.mean())

    def clear_cache(self) -> None:
        """Clear cached rasterized masks."""
        self._masks.clear()


def resolve_roi(roi_config=None) -> BedROI:
    """Resolve ROI from a BedROI, polygon sequence, JSON path, env var, or default JSON."""
    if isinstance(roi_config, BedROI):
        return roi_config
    if roi_config is None:
        env_path = os.environ.get(ROI_ENV_VAR)
        return BedROI.from_json(env_path if env_path else DEFAULT_ROI_PATH)
    if isinstance(roi_config, (str, Path)):
        return BedROI.from_json(roi_config)
    return BedROI(roi_config)


def resolve_weights_path(weights: str | Path) -> str:
    """Resolve bare weight filenames into the package models/ folder when present."""
    path = Path(weights)
    if path.is_file() or path.parent != Path('.'):
        return str(path)
    if DEFAULT_MODELS_DIR.is_dir():
        return str(DEFAULT_MODELS_DIR / path.name)
    return str(weights)


def validate_bgr_frame(frame) -> tuple[int, int]:
    """Validate HxWx3 uint8 BGR numpy image and return (height, width)."""
    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape[2] != 3
        or frame.dtype != np.uint8
        or frame.size == 0
    ):
        raise ValueError('frame must be a non-empty HxWx3 uint8 BGR numpy array')
    return frame.shape[:2]


def box_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """Compute IoU between two xyxy bounding boxes."""
    lo = np.maximum(box_a[:2], box_b[:2])
    hi = np.minimum(box_a[2:], box_b[2:])
    inter = float(np.prod(np.maximum(hi - lo, 0.0)))
    area_a = float(np.prod(np.maximum(box_a[2:] - box_a[:2], 0.0)))
    area_b = float(np.prod(np.maximum(box_b[2:] - box_b[:2], 0.0)))
    return inter / max(area_a + area_b - inter, 1e-9)


class PatientTracker:
    """Select and track the bed occupant without drifting onto entering visitors."""

    def __init__(
        self,
        bed_roi: BedROI,
        min_bed_overlap: float = 0.2,
        min_track_iou: float = 0.15,
        max_lost_frames: int = 5,
    ):
        if not 0.0 <= min_bed_overlap <= 1.0:
            raise ValueError('min_bed_overlap must be in [0, 1]')
        if not 0.0 <= min_track_iou <= 1.0:
            raise ValueError('min_track_iou must be in [0, 1]')
        if max_lost_frames < 1:
            raise ValueError('max_lost_frames must be >= 1')
        self.bed_roi = bed_roi
        self.min_bed_overlap = min_bed_overlap
        self.min_track_iou = min_track_iou
        self.max_lost_frames = max_lost_frames
        self.previous_box: np.ndarray | None = None
        self.lost_frames = 0

    def reset(self) -> None:
        self.previous_box = None
        self.lost_frames = 0

    def _register_loss(self, reason: str) -> None:
        # Reset lock only after repeated misses so brief detector flicker keeps tracking,
        # while a patient who left and later returns can re-acquire on the bed.
        if self.previous_box is not None:
            self.lost_frames += 1
            if self.lost_frames >= self.max_lost_frames:
                self.reset()
        raise RuntimeError(reason)

    def select(self, boxes: np.ndarray, shape: tuple[int, int]) -> int:
        """Return index of the tracked patient box, or raise RuntimeError when unavailable."""
        if len(boxes) == 0:
            self._register_loss('No person detected')

        valid_indices = [
            idx
            for idx, box in enumerate(boxes)
            if np.isfinite(box).all() and np.all(box[2:] > box[:2])
        ]
        if not valid_indices:
            self._register_loss('No valid person bounding box detected')

        if self.previous_box is not None:
            scored = [
                (box_iou(self.previous_box, boxes[idx]), idx)
                for idx in valid_indices
            ]
            best_score, best_idx = max(scored, key=lambda item: item[0])
            if best_score < self.min_track_iou:
                self._register_loss('Tracked patient lost')
        else:
            scored = [
                (1.0 - self.bed_roi.box_outside_fraction(boxes[idx], shape), idx)
                for idx in valid_indices
            ]
            best_score, best_idx = max(scored, key=lambda item: item[0])
            if best_score < self.min_bed_overlap:
                raise RuntimeError('No person overlapping bed ROI')

        self.previous_box = np.asarray(boxes[best_idx], dtype=np.float32).copy()
        self.lost_frames = 0
        return best_idx


def _to_numpy(array_like) -> np.ndarray:
    """Convert a torch Tensor or numpy array to a CPU numpy array."""
    if hasattr(array_like, 'detach'):
        array_like = array_like.detach()
    if hasattr(array_like, 'cpu'):
        array_like = array_like.cpu()
    return np.asarray(array_like)


def draw_roi_and_banner(
    canvas: np.ndarray,
    bed_roi: BedROI,
    label: str,
    banner_color: tuple[int, int, int],
) -> None:
    """Draw safe bed ROI polygon and top status banner onto canvas in-place."""
    h, w = canvas.shape[:2]
    poly = np.rint(bed_roi.polygon_pixels((h, w))).astype(np.int32)
    cv2.polylines(canvas, [poly], True, (255, 200, 0), 2)

    font_scale = max(0.5, min(1.0, w / 950.0))
    thickness = max(1, round(font_scale * 2))
    (text_w, text_h), baseline = cv2.getTextSize(
        label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )
    pad = 10
    cv2.rectangle(canvas, (0, 0), (min(w, text_w + pad * 2), text_h + baseline + pad * 2), (20, 20, 20), -1)
    cv2.putText(
        canvas,
        label,
        (pad, text_h + pad),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        banner_color,
        thickness,
        cv2.LINE_AA,
    )


class BaseYoloBedExitMethod(BedExitMethod):
    """Shared Ultralytics YOLO inference, ROI tracking, and rendering base."""

    method_label = 'yolo'

    def __init__(
        self,
        weights: str | Path,
        task: str,
        outside_threshold: float,
        roi_config=None,
        conf: float = 0.35,
        imgsz: int = 480,
        device: str = 'cpu',
        min_bed_overlap: float = 0.2,
        min_track_iou: float = 0.15,
        model=None,
    ) -> None:
        if not 0.0 <= outside_threshold <= 1.0:
            raise ValueError('outside_threshold must be in [0, 1]')
        if not 0.0 < conf <= 1.0:
            raise ValueError('conf must be in (0, 1]')
        if imgsz <= 0:
            raise ValueError('imgsz must be > 0')

        self.bed_roi = resolve_roi(roi_config)
        self.tracker = PatientTracker(
            self.bed_roi,
            min_bed_overlap=min_bed_overlap,
            min_track_iou=min_track_iou,
        )
        self.outside_threshold = outside_threshold
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self.last_result: MethodDiagnostics | None = None

        if model is None:
            from ultralytics import YOLO

            model = YOLO(resolve_weights_path(weights), task=task)
        self.model = model

    def reset(self, roi_config=None) -> None:
        """Reset tracking state (and optionally swap bed ROI between independent images)."""
        if roi_config is not None:
            self.bed_roi = resolve_roi(roi_config)
            self.tracker.bed_roi = self.bed_roi
        self.tracker.reset()
        self.last_result = None

    def judge(self, frame) -> bool:
        """Return True when bed-exit criteria trigger, False when safe."""
        try:
            shape = validate_bgr_frame(frame)
            if self.model is None:
                raise RuntimeError('Detector has been closed')

            results = self.model.predict(
                source=frame,
                classes=[0],
                conf=self.conf,
                imgsz=self.imgsz,
                device=self.device,
                retina_masks=False,
                verbose=False,
            )
            result = results[0]
            if result.boxes is None or len(result.boxes) == 0:
                boxes = np.empty((0, 4), dtype=np.float32)
                confs = np.empty((0,), dtype=np.float32)
            else:
                boxes = _to_numpy(result.boxes.xyxy).astype(np.float32, copy=False)
                raw_confs = getattr(result.boxes, 'conf', None)
                confs = (
                    np.full((len(boxes),), np.nan, dtype=np.float32)
                    if raw_confs is None
                    else _to_numpy(raw_confs).astype(np.float32, copy=False)
                )

            patient_idx = self.tracker.select(boxes, shape)
            confidence = None if np.isnan(confs[patient_idx]) else float(confs[patient_idx])
            diag = self._evaluate_patient(
                result, patient_idx, boxes[patient_idx], confidence, shape
            )
            self.last_result = diag
            return bool(diag.unsafe)
        except Exception as exc:
            self.last_result = MethodDiagnostics(method=self.method_label, unsafe=None, reason=str(exc))
            raise

    @abstractmethod
    def _evaluate_patient(
        self,
        result,
        patient_idx: int,
        patient_box: np.ndarray,
        confidence: float | None,
        shape: tuple[int, int],
    ) -> MethodDiagnostics:
        """Evaluate bed exit and populate MethodDiagnostics for the selected patient."""

    @abstractmethod
    def render(self, frame: np.ndarray) -> np.ndarray:
        """Return a new annotated BGR image visualizing this method's representation."""

    def close(self) -> None:
        """Release loaded model reference and cached ROI masks."""
        self.model = None
        self.bed_roi.clear_cache()
        self.last_result = None
