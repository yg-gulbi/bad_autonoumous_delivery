"""원본 방향 그대로 커스텀/파인튜닝 Segmentation .pt를 비교하는 메서드."""

from eval_methods.base import apply_selector_min_iou, load_eval_runtime
from part5_bed_exit.methods.bed_exit_core import SegmentationROI


class BaselineSegmentation(SegmentationROI):
    """원본 방향 + 원하는 가중치·장치·임계치로 실행하는 Segmentation 비교 메서드."""

    def __init__(self, *, model=None):
        cfg = load_eval_runtime('segmentation', rotate_ccw90=False)
        super().__init__(
            cfg['roi'],
            cfg['weights'],
            outside_fraction=cfg['outside_fraction'],
            device=cfg['device'],
            confidence=cfg['confidence'],
            model=model,
        )
        apply_selector_min_iou(self, cfg['roi'], cfg['min_iou'])
