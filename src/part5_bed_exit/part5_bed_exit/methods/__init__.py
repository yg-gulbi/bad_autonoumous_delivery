"""Detection and segmentation strategies for the Part 5 bed-exit runtime.

The core contains inference/geometry only; BedExitState owns the ROS vote.
"""

from part5_bed_exit.methods.detection_method import DetectionBedExitMethod
from part5_bed_exit.methods.segmentation_method import SegmentationBedExitMethod

__all__ = ['DetectionBedExitMethod', 'SegmentationBedExitMethod']
