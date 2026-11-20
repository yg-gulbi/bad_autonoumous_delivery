"""들어오는 프레임을 왼쪽(반시계) 90도 회전해 YOLO Segmentation을 비교하는 메서드.

머리(오른쪽)·발(왼쪽) 가로 웹캠 프레임을 머리(위)·발(아래) 정립으로 회전하며,
기존 ``cam_roi.json``의 ROI 좌표도 자동으로 ``[y, 1 - x]`` 회전 적용한다.
"""

from eval_methods.base import (
    apply_selector_min_iou,
    load_eval_runtime,
    rotate_frame_ccw90,
)
from part5_bed_exit.methods.bed_exit_core import SegmentationROI


class Rot90Segmentation(SegmentationROI):
    """왼쪽 90도 회전 정립 추론용 Segmentation 비교 메서드."""

    def __init__(self, *, model=None):
        cfg = load_eval_runtime('segmentation', rotate_ccw90=True)
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
