"""목적: ``bed_exit_node.py``의 노드 초기화·프레임 처리·자원 정리를 검증한다.

대상은 ``BedExitNode.__init__()``, ``process_frame()``, ``_release_capture()``와
``_close_detector()``이다. 카메라, 판단 객체, 발행자와 동적 로더는 대역으로 격리한다.
"""

from types import SimpleNamespace

import numpy as np
import rclpy

from part5_bed_exit import bed_exit_node
from part5_bed_exit.bed_exit_state import BedExitState


class _Capture:
    def __init__(self, frame=None):
        self.frame = frame
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        return True, self.frame

    def release(self):
        self.released = True


class _Method:
    def __init__(self, decision=False):
        self.decision = decision
        self.closed = False

    def judge(self, frame):
        return self.decision

    def close(self):
        self.closed = True


def _start_ros(args=None):
    if not rclpy.ok():
        rclpy.init(args=args)


def _stop_ros():
    if rclpy.ok():
        rclpy.shutdown()


def test_init_creates_30hz_timer_with_injected_method(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: capture)
    _start_ros()
    node = None
    try:
        node = bed_exit_node.BedExitNode(detector=_Method())
        assert node.get_parameter('capture_fps').value == 30.0
        assert abs(node.timer.timer_period_ns - round(1_000_000_000 / 30.0)) <= 1
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


def test_init_passes_method_parameters_to_loader(monkeypatch):
    calls = []
    detector = _Method()
    monkeypatch.setattr(
        bed_exit_node,
        'load_bed_exit_method',
        lambda method, config: calls.append((method, config)) or detector,
    )
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: _Capture())
    _start_ros([
        '--ros-args', '-p', 'method:=test_package.detector:Method',
        '-p', 'show:=false',
    ])
    node = None
    try:
        node = bed_exit_node.BedExitNode()
        assert calls == [('test_package.detector:Method', '')]
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


def test_init_uses_main_video_path_without_removing_dynamic_method(monkeypatch):
    capture = _Capture()
    opened_sources = []
    monkeypatch.setattr(
        bed_exit_node.cv2,
        'VideoCapture',
        lambda source: opened_sources.append(source) or capture,
    )
    _start_ros(['--ros-args', '-p', 'show:=false'])
    node = None
    try:
        node = bed_exit_node.BedExitNode(
            detector=_Method(), video_path='sample.mp4')
        assert opened_sources == ['sample.mp4']
        assert node.is_video_file is True
        assert node.loop_video is True
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


def test_process_frame_publishes_five_frame_vote():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    published = []
    node = SimpleNamespace(
        capture=_Capture(frame),
        detector=_Method(True),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=lambda message: None),
    )

    for _ in range(5):
        bed_exit_node.BedExitNode.process_frame(node)

    assert published == [False, False, False, False, True]


def test_process_frame_resets_history_and_skips_publish_on_camera_failure():
    published = []
    capture = SimpleNamespace(read=lambda: (False, None))
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=capture,
        detector=_Method(True),
        state=state,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=lambda message: None),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert published == []
    assert list(node.state.history) == []


def test_process_frame_restarts_looping_video_and_resets_detector():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    reads = iter(((False, None), (True, frame)))
    seek_calls = []
    reset_calls = []
    published = []
    capture = SimpleNamespace(
        read=lambda: next(reads),
        set=lambda prop, value: seek_calls.append((prop, value)) or True,
    )
    detector = SimpleNamespace(
        judge=lambda current_frame: False,
        reset=lambda: reset_calls.append(True),
    )
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=capture,
        detector=detector,
        state=state,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        is_video_file=True,
        loop_video=True,
        get_logger=lambda: SimpleNamespace(error=lambda message: None),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert seek_calls == [(bed_exit_node.cv2.CAP_PROP_POS_FRAMES, 0)]
    assert reset_calls == [True]
    assert published == [False]
    assert list(node.state.history) == [False]


def test_process_frame_contains_video_loop_restart_exception():
    errors = []
    state = BedExitState()
    state.update(True)
    capture = SimpleNamespace(
        read=lambda: (False, None),
        set=lambda prop, value: (_ for _ in ()).throw(RuntimeError('seek failed')),
    )
    node = SimpleNamespace(
        capture=capture,
        detector=_Method(True),
        state=state,
        publisher=SimpleNamespace(publish=lambda message: None),
        is_video_file=True,
        loop_video=True,
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert list(node.state.history) == []
    assert errors == [
        'Video loop restart failed; no frame decision published: seek failed'
    ]


def test_process_frame_resets_history_and_skips_publish_on_method_failure():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    published = []
    detector = _Method()
    detector.judge = lambda current_frame: np.bool_(True)
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=_Capture(frame),
        detector=detector,
        state=state,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=lambda message: None),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert published == []
    assert list(node.state.history) == []


def test_process_frame_contains_publish_failure_and_rewarms():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    errors = []
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=_Capture(frame),
        detector=_Method(True),
        state=state,
        publisher=SimpleNamespace(
            publish=lambda message: (_ for _ in ()).throw(RuntimeError('DDS failure'))
        ),
        show=False,
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert list(node.state.history) == []
    assert errors == ['Publish failed; history reset and no decision published: DDS failure']


def test_process_frame_contains_preview_failure_after_publish():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    published = []
    errors = []
    node = SimpleNamespace(
        capture=_Capture(frame),
        detector=_Method(False),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        show=True,
        get_logger=lambda: SimpleNamespace(error=errors.append),
        _render_preview=lambda current_frame, unsafe: (
            _ for _ in ()
        ).throw(RuntimeError('plugin render failure')),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert published == [False]
    assert node.show is False
    assert errors == ['Preview disabled after rendering failure: plugin render failure']


def test_release_and_close_helpers_release_injected_resources():
    capture = _Capture()
    detector = _Method()
    node = SimpleNamespace(capture=capture, detector=detector, show=False)

    bed_exit_node.BedExitNode._release_capture(node)
    bed_exit_node.BedExitNode._close_detector(node)

    assert capture.released is True
    assert detector.closed is True
    assert node.capture is None
    assert node.detector is None
