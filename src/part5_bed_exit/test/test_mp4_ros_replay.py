"""Test the standalone MP4-to-ROS boundary without a model or ROS discovery."""

from types import SimpleNamespace

import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState
from research.integration.mp4_ros_replay import Mp4RosReplayNode


def test_replay_publishes_shared_five_frame_vote_and_stops_at_eof():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frames = [(True, frame)] * 5 + [(False, None)]
    published = []
    timer = SimpleNamespace(cancelled=False)
    timer.cancel = lambda: setattr(timer, 'cancelled', True)
    logger = SimpleNamespace(info=lambda message: None, error=lambda message: None)
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: frames.pop(0)),
        detector=SimpleNamespace(judge=lambda image: True),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        timer=timer,
        finished=False,
        show=False,
        get_logger=lambda: logger,
    )

    for _ in range(6):
        Mp4RosReplayNode.process_frame(node)

    assert published == [False, False, False, False, True]
    assert node.finished is True
    assert timer.cancelled is True
    assert not node.state.history


def test_replay_keeps_rendering_when_judgment_is_unavailable():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    rendered = []
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (True, frame)),
        detector=SimpleNamespace(
            judge=lambda image: (_ for _ in ()).throw(RuntimeError('no_person'))),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: None),
        get_logger=lambda: SimpleNamespace(error=lambda message: None),
        _render_preview=lambda image, unsafe, reason: rendered.append((unsafe, reason)),
    )

    Mp4RosReplayNode.process_frame(node)

    assert rendered == [(None, 'no_person')]
    assert not node.state.history
