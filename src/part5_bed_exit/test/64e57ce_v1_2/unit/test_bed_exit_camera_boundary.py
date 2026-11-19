"""카메라·판단 예외 시 노드가 중단되지 않고 투표를 재시작하는지 검증한다."""

from types import SimpleNamespace

import numpy as np

from part5_bed_exit.bed_exit_node import BedExitNode
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable


def test_ros_boundary_skips_camera_exceptions_and_bad_returns_then_rewarms():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    capture = SimpleNamespace(read=lambda: (True, frame))
    detector = SimpleNamespace(judge=lambda current_frame: True)
    published = []
    errors = []
    node = SimpleNamespace(
        capture=capture,
        detector=detector,
        state=BedExitState(),
        publisher=SimpleNamespace(
            publish=lambda message: published.append(message.data)
        ),
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )

    for _ in range(5):
        BedExitNode.process_frame(node)
    assert published == [False] * 4 + [True]

    def disconnected_camera():
        raise RuntimeError('camera disconnected')

    capture.read = disconnected_camera
    BedExitNode.process_frame(node)
    assert len(published) == 5
    assert not node.state.history

    capture.read = lambda: (True, frame)
    detector.judge = lambda current_frame: np.bool_(True)
    BedExitNode.process_frame(node)
    assert len(published) == 5
    assert not node.state.history

    def unavailable_judgment(current_frame):
        raise JudgmentUnavailable('no_person')

    detector.judge = unavailable_judgment
    BedExitNode.process_frame(node)
    assert len(published) == 5
    assert not node.state.history

    detector.judge = lambda current_frame: True
    for _ in range(5):
        BedExitNode.process_frame(node)

    assert published[5:] == [False] * 4 + [True]
    assert len(errors) == 3
