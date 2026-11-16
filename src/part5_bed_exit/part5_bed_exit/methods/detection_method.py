"""Bounding-box detection + ROI bed-exit method (yolo11n.pt)."""

from __future__ import annotations

import cv2
import numpy as np

from part5_bed_exit.methods.common import (
    BaseYoloBedExitMethod,
    MethodDiagnostics,
    draw_roi_and_banner,
)


class DetectionBedExitMethod(BaseYoloBedExitMethod):
    """RGB -> person BBox -> center containment & ROI overlap ratio -> warning."""

    method_label = 'detection'

    def __init__(
        self,
        weights: str = 'yolo11n.pt',
        roi_config=None,
        outside_threshold: float = 0.25,
        require_center_inside: bool = True,
        conf: float = 0.35,
        imgsz: int = 480,
        device: str = 'cpu',
        min_bed_overlap: float = 0.2,
        min_track_iou: float = 0.15,
        model=None,
    ) -> None:
        super().__init__(
            weights=weights,
            task='detect',
            outside_threshold=outside_threshold,
            roi_config=roi_config,
            conf=conf,
            imgsz=imgsz,
            device=device,
            min_bed_overlap=min_bed_overlap,
            min_track_iou=min_track_iou,
            model=model,
        )
        self.require_center_inside = require_center_inside

    def _evaluate_patient(
        self,
        result,
        patient_idx: int,
        patient_box: np.ndarray,
        confidence: float | None,
        shape: tuple[int, int],
    ) -> MethodDiagnostics:
        x1, y1, x2, y2 = (float(v) for v in patient_box[:4])
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        center_inside = self.bed_roi.contains(center, shape)
        outside_ratio = self.bed_roi.box_outside_fraction(patient_box, shape)
        overlap_ratio = 1.0 - outside_ratio

        center_triggered = self.require_center_inside and not center_inside
        overlap_triggered = outside_ratio > self.outside_threshold
        unsafe = bool(center_triggered or overlap_triggered)
        reason = 'center_or_overlap_exit' if unsafe else 'inside_bed_roi'

        return MethodDiagnostics(
            method=self.method_label,
            unsafe=unsafe,
            reason=reason,
            confidence=confidence,
            bbox=(x1, y1, x2, y2),
            center=center,
            center_inside=center_inside,
            overlap_ratio=overlap_ratio,
            outside_ratio=outside_ratio,
        )

    def render(self, frame: np.ndarray) -> np.ndarray:
        """Render bed ROI, person bounding box, center dot, and overlap status."""
        canvas = frame.copy()
        diag = self.last_result
        if diag is None or diag.unsafe is None or diag.bbox is None:
            reason = 'not_run' if diag is None else diag.reason
            draw_roi_and_banner(canvas, self.bed_roi, f'DET | UNAVAILABLE ({reason})', (0, 165, 255))
            return canvas

        color = (0, 0, 255) if diag.unsafe else (0, 200, 0)
        x1, y1, x2, y2 = (int(round(v)) for v in diag.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 3)

        if diag.center is not None:
            cx, cy = int(round(diag.center[0])), int(round(diag.center[1]))
            center_color = (0, 220, 0) if diag.center_inside else (0, 0, 255)
            cv2.circle(canvas, (cx, cy), 7, center_color, -1)
            cv2.circle(canvas, (cx, cy), 9, (255, 255, 255), 2)

        verdict = 'EXIT(True)' if diag.unsafe else 'SAFE(False)'
        center_tag = 'IN' if diag.center_inside else 'OUT'
        conf_part = '' if diag.confidence is None else f' | conf={diag.confidence:.2f}'
        label = (
            f'DET | {verdict} | overlap={diag.overlap_ratio * 100:.1f}% '
            f'| center={center_tag}{conf_part}'
        )
        draw_roi_and_banner(canvas, self.bed_roi, label, color)
        return canvas
