"""Pretrained strategies reused from rokey_ws/vision/bed_exit and local YOLO ROI methods.

The core contains inference/geometry only; BedExitState owns the ROS vote.
"""

from part5_bed_exit.methods.detection_method import DetectionBedExitMethod
from part5_bed_exit.methods.segmentation_method import SegmentationBedExitMethod

__all__ = ['DetectionBedExitMethod', 'SegmentationBedExitMethod']
