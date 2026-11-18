import numpy as np
import pytest

from part6_obstacle_classifier.obstacle_classifier import (
    Detection, build_error, load_classifier, process_frame,
)


class FakeClassifier:
    def __init__(self, detections):
        self.detections = detections

    def classify(self, frame):
        return self.detections


@pytest.mark.parametrize('detections,present', [
    ([], False),
    ([Detection('person', 0.9, (1, 2, 8, 9))], True),
])
def test_valid_results_preserve_header(detections, present):
    header = {'frame_id': 'camera', 'stamp': {'sec': 1, 'nanosec': 2}}
    result = process_frame(np.zeros((10, 10, 3), dtype=np.uint8),
                           FakeClassifier(detections), header)
    assert result['obstacle_present'] is present
    assert result['header'] == header
    assert len(result['detections']) == len(detections)


@pytest.mark.parametrize('frame', [None, np.zeros((0, 2, 3), dtype=np.uint8),
                                     np.zeros((2, 2)), np.zeros((2, 2, 4))])
def test_invalid_image_is_rejected(frame):
    with pytest.raises(ValueError):
        process_frame(frame, FakeClassifier([]), {})


@pytest.mark.parametrize('detection', [
    Detection('person', float('nan'), (0, 0, 1, 1)),
    Detection('person', 1.1, (0, 0, 1, 1)),
    Detection('', 0.5, (0, 0, 1, 1)),
    Detection('person', 0.5, (0, 0, 11, 1)),
    Detection('person', 0.5, (1, 0, 0, 1)),
])
def test_invalid_model_result_is_rejected(detection):
    with pytest.raises(ValueError):
        process_frame(np.zeros((10, 10, 3), dtype=np.uint8),
                      FakeClassifier([detection]), {})


def test_unconfigured_model_does_not_report_clear():
    with pytest.raises(RuntimeError):
        process_frame(np.zeros((10, 10, 3), dtype=np.uint8), load_classifier(''), {})
    assert build_error('processing_error', 'model unavailable')['obstacle_present'] is None


def test_valid_depth_includes_pose_and_invalid_depth_omits_pose():
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    depth = np.zeros((10, 10), dtype=np.uint16)
    depth[3, 3] = 2000  # 2.0m (valid)
    depth[7, 7] = 0     # invalid
    K = np.array([[100.0, 0.0, 5.0], [0.0, 100.0, 5.0], [0.0, 0.0, 1.0]])
    detections = [
        Detection('person', 0.9, (2, 2, 4, 4)),
        Detection('chair', 0.8, (6, 6, 8, 8)),
    ]

    result = process_frame(
        frame,
        FakeClassifier(detections),
        {'frame_id': 'camera'},
        depth_image=depth,
        K=K,
        transform_fn=lambda x, y, z: {'x': x + 1.0, 'y': y, 'z': z},
    )

    assert result['obstacle_present'] is True
    assert 'pose' in result['detections'][0]
    assert result['detections'][0]['pose']['z'] == pytest.approx(2.0)
    assert 'pose' not in result['detections'][1]
