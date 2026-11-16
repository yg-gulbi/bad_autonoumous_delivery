"""GPU instance segmentation + bed-boundary ROI bed-exit method (yolo11n-seg.pt)."""

from __future__ import annotations

import cv2
import numpy as np

from part5_bed_exit.methods.common import (
    BaseYoloBedExitMethod,
    MethodDiagnostics,
    _to_numpy,
    draw_roi_and_banner,
)


def _crop_letterbox_mask(mask: np.ndarray, orig_shape: tuple[int, int]) -> np.ndarray:
    """Strip Ultralytics letterbox padding from a raw mask without full-res upsampling."""
    im0_h, im0_w = orig_shape
    im1_h, im1_w = mask.shape
    if (im1_h, im1_w) == (im0_h, im0_w):
        return mask

    gain = min(im1_h / im0_h, im1_w / im0_w)
    pad_w = (im1_w - round(im0_w * gain)) / 2.0
    pad_h = (im1_h - round(im0_h * gain)) / 2.0
    top = max(0, round(pad_h - 0.1))
    left = max(0, round(pad_w - 0.1))
    bottom = min(im1_h, top + max(1, round(im0_h * gain)))
    right = min(im1_w, left + max(1, round(im0_w * gain)))
    return mask[top:bottom, left:right]


class SegmentationBedExitMethod(BaseYoloBedExitMethod):
    """RGB -> person instance mask -> unsafe when mask crosses outside the bed ROI."""

    method_label = 'segmentation'

    def __init__(
        self,
        weights: str = 'yolo11n-seg.pt',
        roi_config=None,
        outside_threshold: float = 0.05,
        conf: float = 0.25,
        imgsz: int = 640,
        device: str | None = 'auto',
        min_bed_overlap: float = 0.2,
        min_track_iou: float = 0.15,
        max_center_jump: float = 0.25,
        mask_max_dim: int | None = 160,
        model=None,
    ) -> None:
        if mask_max_dim is not None and mask_max_dim <= 0:
            raise ValueError('mask_max_dim must be > 0 or None')
        super().__init__(
            weights=weights,
            task='segment',
            outside_threshold=outside_threshold,
            roi_config=roi_config,
            conf=conf,
            imgsz=imgsz,
            device=device,
            min_bed_overlap=min_bed_overlap,
            min_track_iou=min_track_iou,
            max_center_jump=max_center_jump,
            model=model,
        )
        self.mask_max_dim = mask_max_dim

    def _evaluate_patient(
        self,
        result,
        patient_idx: int,
        patient_box: np.ndarray,
        confidence: float | None,
        shape: tuple[int, int],
    ) -> MethodDiagnostics:
        masks = getattr(result, 'masks', None)
        mask_data = None if masks is None else getattr(masks, 'data', None)
        if mask_data is None or patient_idx >= len(mask_data):
            raise RuntimeError('Missing segmentation mask for tracked patient')

        raw_mask = _to_numpy(mask_data[patient_idx])
        if raw_mask.ndim != 2 or raw_mask.size == 0:
            raise RuntimeError('Invalid segmentation mask dimensions')

        cropped = _crop_letterbox_mask(raw_mask, shape)
        full_res_mask = cropped > 0.5
        person_mask = full_res_mask
        if self.mask_max_dim is not None and max(person_mask.shape) > self.mask_max_dim:
            scale = self.mask_max_dim / max(person_mask.shape)
            eval_h = max(1, round(person_mask.shape[0] * scale))
            eval_w = max(1, round(person_mask.shape[1] * scale))
            person_mask = cv2.resize(
                person_mask.astype(np.uint8),
                (eval_w, eval_h),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)

        mask_area = int(np.count_nonzero(person_mask))
        if mask_area == 0:
            raise RuntimeError('Empty segmentation mask for tracked patient')

        roi_mask = self.bed_roi.mask(person_mask.shape)
        outside_pixels = int(np.count_nonzero(person_mask & ~roi_mask))
        outside_ratio = float(outside_pixels / mask_area)
        unsafe = bool(outside_ratio > self.outside_threshold)
        reason = 'mask_outside_threshold' if unsafe else 'mask_inside_bed_roi'

        return MethodDiagnostics(
            method=self.method_label,
            unsafe=unsafe,
            reason=reason,
            confidence=confidence,
            bbox=tuple(float(v) for v in patient_box[:4]),
            overlap_ratio=1.0 - outside_ratio,
            outside_ratio=outside_ratio,
            mask=full_res_mask,
        )

    def render(self, frame: np.ndarray) -> np.ndarray:
        """Render bed ROI, pixel mask (green inside / red outside), contour, and outside ratio."""
        canvas = frame.copy()
        diag = self.last_result
        if diag is None or diag.unsafe is None or diag.mask is None:
            reason = 'not_run' if diag is None else diag.reason
            draw_roi_and_banner(canvas, self.bed_roi, f'SEG | UNAVAILABLE ({reason})', (0, 165, 255))
            return canvas

        h, w = canvas.shape[:2]
        full_mask = (
            cv2.resize(
                diag.mask.astype(np.float32),
                (w, h),
                interpolation=cv2.INTER_LINEAR,
            )
            > 0.5
        )
        roi_mask = self.bed_roi.mask((h, w))
        inside_mask = full_mask & roi_mask
        outside_mask = full_mask & ~roi_mask

        overlay = canvas.copy()
        overlay[inside_mask] = (0, 200, 0)
        overlay[outside_mask] = (0, 0, 255)
        cv2.addWeighted(overlay, 0.42, canvas, 0.58, 0.0, dst=canvas)

        contours, _ = cv2.findContours(
            full_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(canvas, contours, -1, (255, 255, 255), 2)

        color = (0, 0, 255) if diag.unsafe else (0, 200, 0)
        verdict = 'UNSAFE(True)' if diag.unsafe else 'SAFE(False)'
        conf_part = '' if diag.confidence is None else f' | conf={diag.confidence:.2f}'
        label = (
            f'SEG | {verdict} | outside={diag.outside_ratio * 100:.1f}% '
            f'(thr={self.outside_threshold * 100:.0f}%){conf_part}'
        )
        draw_roi_and_banner(canvas, self.bed_roi, label, color)
        return canvas
