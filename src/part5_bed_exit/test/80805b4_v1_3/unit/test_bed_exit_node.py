"""목적: ``bed_exit_node.py``의 노드 초기화·프레임 처리·자원 정리를 검증한다.

대상은 ``BedExitNode.__init__()``, ``process_frame()``, ``_release_capture()``와
``_close_detector()``이다. 카메라, 판단 객체, 발행자와 동적 로더는 대역으로 격리한다.
"""

from types import SimpleNamespace

import numpy as np
import pytest
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
        assert node.get_parameter('loop_video').value is False
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


def test_process_frame_stops_video_at_eof_without_publish():
    published = []
    info_logs = []
    timer = SimpleNamespace(cancelled=False)
    timer.cancel = lambda: setattr(timer, 'cancelled', True)
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=SimpleNamespace(read=lambda: (False, None)),
        detector=_Method(True),
        state=state,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        is_video_file=True,
        timer=timer,
        get_logger=lambda: SimpleNamespace(
            error=lambda message: None, info=info_logs.append),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert published == []
    assert list(node.state.history) == []
    assert timer.cancelled is True
    assert info_logs == ['Video playback finished; stopping timer']


def test_process_frame_rewinds_loop_video_and_rewarms_without_stopping():
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    published = []
    info_logs = []
    reads = iter([(False, None), (True, frame)])
    seek_calls = []
    timer = SimpleNamespace(cancelled=False)
    timer.cancel = lambda: setattr(timer, 'cancelled', True)
    detector = _Method(False)
    detector.reset_count = 0
    detector.reset = lambda: setattr(
        detector, 'reset_count', detector.reset_count + 1)
    state = BedExitState()
    state.update(True)
    node = SimpleNamespace(
        capture=SimpleNamespace(
            read=lambda: next(reads),
            set=lambda prop, value: seek_calls.append((prop, value)) or True,
        ),
        detector=detector,
        state=state,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        is_video_file=True,
        loop_video=True,
        timer=timer,
        get_logger=lambda: SimpleNamespace(
            error=lambda message: None, info=info_logs.append),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert seek_calls == [(bed_exit_node.cv2.CAP_PROP_POS_FRAMES, 0)]
    assert detector.reset_count == 1
    assert list(node.state.history) == [False]
    assert published == [False]
    assert timer.cancelled is False
    assert info_logs == ['Video playback loop restarted from frame 0']


def test_process_frame_stops_when_loop_rewind_fails():
    errors = []
    timer = SimpleNamespace(cancelled=False)
    timer.cancel = lambda: setattr(timer, 'cancelled', True)
    node = SimpleNamespace(
        capture=SimpleNamespace(
            read=lambda: (False, None),
            set=lambda prop, value: False,
        ),
        detector=_Method(False),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: None),
        is_video_file=True,
        loop_video=True,
        timer=timer,
        get_logger=lambda: SimpleNamespace(
            error=errors.append, info=lambda message: None),
    )

    bed_exit_node.BedExitNode.process_frame(node)

    assert timer.cancelled is True
    assert list(node.state.history) == []
    assert errors == [
        'Video loop restart failed; stopping timer: '
        'capture does not support seeking to frame 0'
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


class _ClosedCapture(_Capture):
    def isOpened(self):
        return False


def test_init_stays_alive_when_webcam_unavailable(monkeypatch):
    closed_capture = _ClosedCapture()
    opened_sources = []
    monkeypatch.setattr(
        bed_exit_node.cv2,
        'VideoCapture',
        lambda source: opened_sources.append(source) or closed_capture,
    )
    _start_ros(['--ros-args', '-p', 'camera_index:=2', '-p', 'show:=false'])
    node = None
    try:
        node = bed_exit_node.BedExitNode(detector=_Method())
        assert opened_sources == [2]
        assert closed_capture.released is True
        assert node.capture is None
        assert node.camera_index == 2
        assert node.reconnect_interval == 1.0
        assert node.timer is not None
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


def test_init_still_raises_when_video_file_missing(monkeypatch):
    detector = _Method()
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda source: _ClosedCapture())
    _start_ros(['--ros-args', '-p', 'show:=false'])
    try:
        with pytest.raises(RuntimeError, match='Cannot open video source'):
            bed_exit_node.BedExitNode(detector=detector, video_path='missing.mp4')
        assert detector.closed is True
    finally:
        _stop_ros()


def test_init_rejects_invalid_reconnect_interval(monkeypatch):
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda source: _Capture())
    _start_ros(['--ros-args', '-p', 'reconnect_interval:=0.0', '-p', 'show:=false'])
    try:
        with pytest.raises(ValueError, match='reconnect_interval'):
            bed_exit_node.BedExitNode(detector=_Method())
    finally:
        _stop_ros()


def test_process_frame_reconnects_after_cooldown(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    clock = [10.0]
    opened_sources = []
    published = []
    info_logs = []
    errors = []
    reconnected_capture = _Capture(frame)

    monkeypatch.setattr(bed_exit_node.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(
        bed_exit_node.cv2,
        'VideoCapture',
        lambda source: opened_sources.append(source) or reconnected_capture,
    )

    node = SimpleNamespace(
        capture=None,
        camera_index=2,
        is_video_file=False,
        reconnect_interval=1.0,
        _last_reconnect_attempt=10.0,
        detector=_Method(False),
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        show=False,
        get_logger=lambda: SimpleNamespace(error=errors.append, info=info_logs.append),
    )

    # 쿨다운 미만(0.5초 경과): VideoCapture 재호출 없이 대기하고 발행하지 않는다.
    clock[0] = 10.5
    bed_exit_node.BedExitNode.process_frame(node)
    assert opened_sources == []
    assert published == []
    assert node.capture is None

    # 쿨다운 경과(1.0초): 재연결 성공 후 즉시 프레임을 처리·발행한다.
    clock[0] = 11.0
    bed_exit_node.BedExitNode.process_frame(node)
    assert opened_sources == [2]
    assert node.capture is reconnected_capture
    assert published == [False]
    assert info_logs == ['Webcam reconnected (camera_index=2)']
    assert errors == []


def test_process_frame_releases_and_reconnects_on_usb_unplug_replug(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    clock = [20.0]
    opened_sources = []
    published = []
    info_logs = []
    errors = []

    cap1 = _Capture(frame)
    cap2 = _Capture(frame)
    detector = _Method(True)
    detector.previous = 'kept_bbox'

    monkeypatch.setattr(bed_exit_node.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(
        bed_exit_node.cv2,
        'VideoCapture',
        lambda source: opened_sources.append(source) or cap2,
    )

    node = SimpleNamespace(
        capture=cap1,
        camera_index=2,
        is_video_file=False,
        reconnect_interval=1.0,
        _last_reconnect_attempt=None,
        detector=detector,
        state=BedExitState(),
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        show=False,
        get_logger=lambda: SimpleNamespace(error=errors.append, info=info_logs.append),
    )

    # 1) 정상 5프레임 발행
    for _ in range(5):
        bed_exit_node.BedExitNode.process_frame(node)
    assert published == [False, False, False, False, True]

    # 2) 실행 중 USB 웹캠 분리(read 실패): 즉시 핸들 해제 + history 초기화 + previous 유지 + 미발행
    cap1.read = lambda: (False, None)
    bed_exit_node.BedExitNode.process_frame(node)
    assert cap1.released is True
    assert node.capture is None
    assert list(node.state.history) == []
    assert detector.previous == 'kept_bbox'
    assert len(published) == 5

    # 3) 쿨다운 전(0.4초): 재오픈 시도하지 않음
    clock[0] = 20.4
    bed_exit_node.BedExitNode.process_frame(node)
    assert opened_sources == []
    assert len(published) == 5

    # 4) 쿨다운 이후(1.1초): USB 재연결 성공 -> previous 유지 상태로 5프레임 재워밍업 후 True 복구
    clock[0] = 21.1
    for _ in range(5):
        bed_exit_node.BedExitNode.process_frame(node)
    assert opened_sources == [2]
    assert node.capture is cap2
    assert detector.previous == 'kept_bbox'
    assert published[5:] == [False, False, False, False, True]
    assert info_logs == ['Webcam reconnected (camera_index=2)']
