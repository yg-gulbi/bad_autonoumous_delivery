"""통합 테스트: MP4 입력부터 5프레임 투표·ROS 발행 경계까지 검증한다.

대상 코드는 ``Mp4RosReplayNode.process_frame()``이다. 실제 모델과 ROS Discovery 대신
가짜 영상 프레임·detector·publisher를 연결해 5프레임 투표 결과 발행과 영상 종료 시
타이머 중단을 확인한다.
"""

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
