"""단위 테스트: ``bed_exit_core.py``의 프리뷰와 ROI 교체를 검증한다.

대상 코드는 ``DetectionROI.render()``, ``SegmentationROI.render()``,
``BedExitMethod.set_roi()``이다. 실제 모델 대신 진단값과 가짜 모델 객체를 넣어
프리뷰 생성과 ROI 변경 시 환자 추적 초기화만 독립적으로 확인한다.
"""

import numpy as np

from part5_bed_exit.methods.bed_exit_core import (
    DetectionROI,
    Diagnostics,
    MediaPipePoseROI,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
    BedExitMethod,
)


def test_core_detection_and_segmentation_render():
    """Detection과 Segmentation이 같은 진단 구조로 프리뷰를 생성한다."""
    roi = PolygonROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    detection = DetectionROI(roi, 'unused.pt', model=object())
    detection.last = Diagnostics(
        valid=True,
        reason='box_outside_fraction',
        score=0.2,
        bbox=(10.0, 30.0, 60.0, 70.0),
    )
    assert not np.array_equal(detection.render(frame), frame)

    mask = np.zeros((100, 100), dtype=np.float32)
    mask[30:70, 30:90] = 1.0
    segmentation = SegmentationROI(roi, 'unused.pt', model=object())
    segmentation.last = Diagnostics(
        valid=True,
        reason='mask_outside_fraction',
        score=0.2,
        bbox=(30.0, 30.0, 90.0, 70.0),
        mask=mask,
    )
    assert not np.array_equal(segmentation.render(frame), frame)


def test_four_methods_render_different_visual_evidence():
    """[UT-RENDER-001] 네 방법론의 결과 영상이 서로 다른 근거를 표시하는지 검증한다."""
    roi = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.float32)
    mask[20:80, 20:80] = 1.0

    detection = DetectionROI(roi, 'unused.pt', model=object())
    segmentation = SegmentationROI(roi, 'unused.pt', model=object())
    yolo_pose = YoloPoseROI(roi, 'unused.pt', model=object())
    # MediaPipe 생성자는 네이티브 모델을 열기 때문에 렌더링 단위 테스트에서는 우회한다.
    mediapipe_pose = MediaPipePoseROI.__new__(MediaPipePoseROI)
    BedExitMethod.__init__(mediapipe_pose, roi)
    mediapipe_pose.point_conf = 0.5

    detection.last = Diagnostics(True, 'box_outside_fraction', 0.0, (20, 20, 80, 80))
    segmentation.last = Diagnostics(True, 'mask_outside_fraction', 0.0, (20, 20, 80, 80), mask=mask)
    yolo_points = np.zeros((17, 3), dtype=np.float32)
    yolo_points[:, :2] = np.linspace((25, 25), (75, 75), 17)
    yolo_points[:, 2] = 0.9
    mp_points = np.zeros((33, 3), dtype=np.float32)
    mp_points[:, :2] = np.linspace((20, 25), (80, 75), 33)
    mp_points[:, 2] = 0.9
    yolo_pose.last = Diagnostics(True, 'keypoints_inside', 0.0, points=yolo_points.tolist())
    mediapipe_pose.last = Diagnostics(True, 'keypoints_inside', 0.0, points=mp_points.tolist())

    rendered = [
        detection.render(frame),
        segmentation.render(frame),
        yolo_pose.render(frame),
        mediapipe_pose.render(frame),
    ]

    assert all(not np.array_equal(image, frame) for image in rendered)
    assert len({image.tobytes() for image in rendered}) == 4


def test_set_roi_resets_patient_tracking():
    """ROI를 교체하면 이전 프레임의 환자 BBox를 제거한다."""
    first = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    second = PolygonROI([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
    method = DetectionROI(first, 'unused.pt', model=object())
    method.selector.previous = np.array([10, 10, 30, 30], dtype=np.float32)

    method.set_roi(second)

    assert method.roi is second
    assert method.selector.previous is None
