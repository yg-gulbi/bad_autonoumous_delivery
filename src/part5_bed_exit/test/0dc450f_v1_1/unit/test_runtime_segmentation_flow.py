"""단위 테스트: 순서도의 마스크 누락 예외 흐름을 검증한다.

대상 코드는 ``SegmentationROI.judge()``와 ``BedExitNode.process_frame()``이다.
환자 BBox는 있지만 마스크가 없는 가짜 후보를 넣어, ROS 발행을 생략하고 5프레임
이력만 초기화하며 다음 프레임용 환자 BBox는 유지하는지 확인한다.
"""

from types import SimpleNamespace

import numpy as np

from part5_bed_exit.bed_exit_node import BedExitNode
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import Candidate, PolygonROI, SegmentationROI


class _MissingMaskSegmentation(SegmentationROI):
    """환자 BBox는 찾았지만 분할 마스크가 없는 프레임을 재현한다."""

    def __init__(self):
        roi = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
        super().__init__(roi, 'unused.pt', model=object())

    def candidates(self, frame):
        """침대 안의 동일 환자 후보를 마스크 없이 반환한다."""
        return [Candidate(np.array([20, 20, 80, 80], dtype=float), 0.9, mask=None)]


class _NoPersonSegmentation(_MissingMaskSegmentation):
    """모델 결과에 사람 후보가 하나도 없는 프레임을 재현한다."""

    def candidates(self, frame):
        return []


def test_missing_mask_resets_vote_but_keeps_patient_position():
    """마스크 누락 시 미발행하고 투표만 초기화하며 환자 위치는 보존한다."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    detector = _MissingMaskSegmentation()
    published = []
    errors = []
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (True, frame)),
        detector=detector,
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )
    node.state.update(True)

    BedExitNode.process_frame(node)

    assert published == []
    assert list(node.state.history) == []
    assert np.array_equal(detector.selector.previous, np.array([20, 20, 80, 80]))
    assert errors and 'missing_mask' in errors[0]


def test_no_person_is_unknown_and_never_published_as_safe_false():
    """[UT-SAFETY-001] 사람 미검출은 정상 False가 아니라 미발행인지 검증한다."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    published = []
    errors = []
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (True, frame)),
        detector=_NoPersonSegmentation(),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )

    BedExitNode.process_frame(node)

    assert published == []
    assert list(node.state.history) == []
    assert errors and 'no_person' in errors[0]
