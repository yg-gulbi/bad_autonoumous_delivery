"""연구용 MP4 ROS 노드의 외부 I/O 예외가 콜백 밖으로 나가지 않는지 검증한다."""

from types import SimpleNamespace

import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState
from research.integration.mp4_ros_replay import Mp4RosReplayNode


def _logger(errors):
    return SimpleNamespace(error=errors.append, info=lambda message: None)


def test_replay_contains_capture_exception_and_resets_history():
    errors = []
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=SimpleNamespace(
            read=lambda: (_ for _ in ()).throw(RuntimeError('decoder disconnected'))
        ),
        state=state,
        publisher=SimpleNamespace(publish=lambda message: None),
        get_logger=lambda: _logger(errors),
    )

    Mp4RosReplayNode.process_frame(node)

    assert list(node.state.history) == []
    assert errors == ['MP4 read failed; history reset: decoder disconnected']


def test_replay_contains_publish_and_preview_exceptions():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    errors = []
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (True, frame)),
        detector=SimpleNamespace(judge=lambda image: True),
        state=BedExitState(),
        publisher=SimpleNamespace(
            publish=lambda message: (_ for _ in ()).throw(RuntimeError('DDS failure'))
        ),
        show=True,
        get_logger=lambda: _logger(errors),
        _render_preview=lambda image, unsafe, reason: (
            _ for _ in ()
        ).throw(RuntimeError('preview failure')),
    )

    Mp4RosReplayNode.process_frame(node)

    assert list(node.state.history) == []
    assert node.show is False
    assert errors == [
        'Publish failed; history reset: DDS failure',
        'Preview disabled after rendering failure: preview failure',
    ]


def test_replay_contains_preview_exception_after_successful_publish():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    published = []
    errors = []
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (True, frame)),
        detector=SimpleNamespace(judge=lambda image: False),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        show=True,
        get_logger=lambda: _logger(errors),
        _render_preview=lambda image, unsafe, reason: (
            _ for _ in ()
        ).throw(RuntimeError('preview failure')),
    )

    Mp4RosReplayNode.process_frame(node)

    assert published == [False]
    assert node.show is False
    assert errors == ['Preview disabled after rendering failure: preview failure']
