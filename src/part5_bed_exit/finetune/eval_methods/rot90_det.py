"""들어오는 프레임을 왼쪽(반시계) 90도 회전해 YOLO Detection(BBox)을 비교하는 메서드."""

from eval_methods.base import (
    apply_selector_min_iou,
    load_eval_runtime,
    rotate_frame_ccw90,
)
from part5_bed_exit.methods.bed_exit_core import DetectionROI


class Rot90Detection(DetectionROI):
    """왼쪽 90도 회전 정립 추론용 Detection(BBox) 비교 메서드."""

    def __init__(self, *, model=None):
        cfg = load_eval_runtime('detection', rotate_ccw90=True)
        super().__init__(
            cfg['roi'],
            cfg['weights'],
            outside_fraction=cfg['outside_fraction'],
            device=cfg['device'],
            confidence=cfg['confidence'],
            model=model,
        )
        apply_selector_min_iou(self, cfg['roi'], cfg['min_iou'])

    def judge(self, frame) -> bool:
        return super().judge(rotate_frame_ccw90(frame))

    def render(self, frame):
        return super().render(rotate_frame_ccw90(frame))
