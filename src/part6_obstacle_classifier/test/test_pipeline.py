# 실제 모델·카메라·ROS 실행 없이 입력 검증과 결과 생성 파이프라인을 검사한다.
import numpy as np
import pytest

from part6_obstacle_classifier.obstacle_classifier import (
    Detection, build_error, load_classifier, process_frame,
)


class FakeClassifier:
    # 모델 추론 대신 미리 지정한 검출 결과를 반환해 파이프라인만 독립적으로 테스트한다.
    def __init__(self, detections):
        self.detections = detections

    def classify(self, frame):
        return self.detections


@pytest.mark.parametrize('detections,present', [
    ([], False),  # 검출 결과가 없으면 장애물 없음으로 보고하는지 검사한다.
    ([Detection('person', 0.9, (1, 2, 8, 9))], True),  # 검출 결과가 있으면 장애물 있음.
])
def test_valid_results_preserve_header(detections, present):
    # 정상 이미지 처리 후 장애물 유무·입력 헤더·검출 개수가 보존되는지 검증한다.
    header = {'frame_id': 'camera', 'stamp': {'sec': 1, 'nanosec': 2}}
    result = process_frame(np.zeros((10, 10, 3), dtype=np.uint8),
                           FakeClassifier(detections), header)
    assert result['obstacle_present'] is present
    assert result['header'] == header
    assert len(result['detections']) == len(detections)


@pytest.mark.parametrize('frame', [None, np.zeros((0, 2, 3), dtype=np.uint8),
                                     np.zeros((2, 2)), np.zeros((2, 2, 4))])
def test_invalid_image_is_rejected(frame):
    # 이미지 누락, 빈 이미지, 단일 채널, 4채널 입력을 각각 검사한다.
    # 파이프라인이 기대하는 3채널 이미지가 아니면 ValueError로 거부해야 한다.
    with pytest.raises(ValueError):
        process_frame(frame, FakeClassifier([]), {})


@pytest.mark.parametrize('detection', [
    Detection('person', float('nan'), (0, 0, 1, 1)),  # 신뢰도가 유효한 수가 아닌 경우.
    Detection('person', 1.1, (0, 0, 1, 1)),  # 신뢰도가 허용 범위 [0, 1]을 벗어나는 경우.
    Detection('', 0.5, (0, 0, 1, 1)),  # 클래스 이름이 비어 있는 경우.
    Detection('person', 0.5, (0, 0, 11, 1)),  # 박스가 이미지 너비 10을 벗어나는 경우.
    Detection('person', 0.5, (1, 0, 0, 1)),  # 박스의 좌우 좌표 순서가 뒤집힌 경우.
])
def test_invalid_model_result_is_rejected(detection):
    # 입력 이미지가 정상이어도 잘못된 모델 출력은 ValueError로 차단하는지 검증한다.
    with pytest.raises(ValueError):
        process_frame(np.zeros((10, 10, 3), dtype=np.uint8),
                      FakeClassifier([detection]), {})


def test_unconfigured_model_does_not_report_clear():
    # 모델을 설정하지 않은 상태를 정상적인 '장애물 없음'으로 처리하지 않는지 검증한다.
    with pytest.raises(RuntimeError):
        process_frame(np.zeros((10, 10, 3), dtype=np.uint8), load_classifier(''), {})
    # 오류 응답은 False(장애물 없음)가 아니라 None(판단 불가)을 사용해야 한다.
    assert build_error('processing_error', 'model unavailable')['obstacle_present'] is None


def test_valid_depth_includes_pose_and_invalid_depth_omits_pose():
    # 동일 프레임에서 깊이가 유효한 검출에만 3D 위치(pose)가 추가되는지 검증한다.
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    depth = np.zeros((10, 10), dtype=np.uint16)
    depth[3, 3] = 2000  # 첫 번째 박스 중심: 2000mm = 2m인 유효한 깊이.
    depth[7, 7] = 0     # 두 번째 박스 중심: 0은 유효하지 않은 깊이.
    # 카메라 내부 파라미터: 초점거리 fx=fy=100, 주점 cx=cy=5.
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
        # 좌표 변환 함수의 대역. 이 테스트에서는 변환 후 z만 확인하며 x 이동은 검증하지 않는다.
        transform_fn=lambda x, y, z: {'x': x + 1.0, 'y': y, 'z': z},
    )

    # 깊이 누락이 있어도 검출된 장애물의 존재 자체는 유지되어야 한다.
    assert result['obstacle_present'] is True
    # 유효 깊이를 가진 사람 검출에는 pose가 있고, mm → m 변환 결과 z가 2m여야 한다.
    assert 'pose' in result['detections'][0]
    assert result['detections'][0]['pose']['z'] == pytest.approx(2.0)
    # 깊이가 0인 의자 검출에는 임의의 위치를 만들지 않고 pose를 생략해야 한다.
    assert 'pose' not in result['detections'][1]
