"""ROS 2 노드·토픽 발행·비동기 반복 TTS·JSON/YAML 파라미터·앙상블 통합 검증."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit import bed_exit_node, tts_speaker
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable
from part5_bed_exit.pretrained import EnsembleBedExitMethod
from part5_bed_exit.tts_speaker import DEFAULT_TTS_MESSAGE


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_YAML = PACKAGE_ROOT / 'config' / 'bed_exit.params.template.yaml'


class _FrameCapture:
    def __init__(self, frame):
        self.frame = frame

    def isOpened(self):
        return True

    def read(self):
        return True, self.frame

    def release(self):
        pass


class _BlockingProc:
    def __init__(self, argv):
        self.argv = argv
        self.started = threading.Event()
        self.allow_finish = threading.Event()
        self.finished = threading.Event()
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.started.set()
        if not self.allow_finish.wait(timeout=timeout):
            raise TimeoutError('mock TTS process wait timed out')
        self.finished.set()
        return 0

    def terminate(self):
        self.terminated = True
        self.allow_finish.set()

    def kill(self):
        self.killed = True
        self.allow_finish.set()


class _Subscriber(Node):
    def __init__(self, topic='/patient/bed_exit'):
        super().__init__('test_bed_exit_sub')
        self.received: list[bool] = []
        self.create_subscription(Bool, topic, self._on_msg, 10)

    def _on_msg(self, msg: Bool):
        self.received.append(bool(msg.data))


def _spin_until(executor, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executor.spin_once(timeout_sec=0.02)
        if predicate():
            return True
    return predicate()


@pytest.mark.integration
def test_legacy_default_invocation_preserves_v13_contract(monkeypatch):
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda src: _FrameCapture(frame))
    monkeypatch.setattr(tts_speaker.shutil, 'which', lambda name: None)

    class _AlwaysExit:
        def judge(self, img):
            return True

    rclpy.init(args=['--ros-args', '-p', 'show:=false'])
    node = sub = executor = None
    try:
        node = bed_exit_node.BedExitNode(detector=_AlwaysExit())
        node.timer.cancel()  # 테스트에서 프레임 타이밍을 결정적으로 제어
        sub = _Subscriber('/patient/bed_exit')
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        executor.add_node(sub)

        assert node.voting_window == 5
        assert node.voting_threshold == 3
        assert node.prefill_safe_queue is False
        assert node.tts.message == DEFAULT_TTS_MESSAGE

        for _ in range(5):
            node.process_frame()
            executor.spin_once(timeout_sec=0.02)

        assert _spin_until(executor, lambda: len(sub.received) >= 5)
        assert sub.received[:5] == [False, False, False, False, True]
    finally:
        if executor is not None:
            executor.shutdown()
        if sub is not None:
            sub.destroy_node()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


@pytest.mark.integration
def test_ros2_pipeline_async_repeating_tts_finishes_utterance_and_stops_on_safe_or_reset(
    tmp_path, monkeypatch
):
    raw_frame = np.zeros((12, 24, 3), dtype=np.uint8)
    seen_shapes: list[tuple[int, int, int]] = []
    procs: list[_BlockingProc] = []
    spawn_events = [threading.Event() for _ in range(4)]

    def fake_popen(argv, **kwargs):
        idx = len(procs)
        proc = _BlockingProc(argv)
        procs.append(proc)
        if idx < len(spawn_events):
            spawn_events[idx].set()
        return proc

    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda src: _FrameCapture(raw_frame))
    monkeypatch.setattr(tts_speaker.shutil, 'which', lambda name: '/usr/bin/spd-say' if name == 'spd-say' else None)
    monkeypatch.setattr(tts_speaker.subprocess, 'Popen', fake_popen)

    # 3모델 앙상블(majority)의 프레임별 시나리오 제어
    mode = {'state': 'exit'}

    class _ScriptedMember:
        def __init__(self, flip_on_safe=False):
            self.flip_on_safe = flip_on_safe

        def judge(self, img):
            seen_shapes.append(img.shape)
            if mode['state'] == 'unavailable':
                raise JudgmentUnavailable('no_person')
            if mode['state'] == 'exit':
                return True
            return self.flip_on_safe  # safe 시 1개만 True여도 3모델 다수결은 False

    ensemble = EnsembleBedExitMethod(
        [_ScriptedMember(False), _ScriptedMember(False), _ScriptedMember(True)],
        policy='majority',
    )

    params_yaml = tmp_path / 'integration_params.yaml'
    params_yaml.write_text(
        """bed_exit:
  ros__parameters:
    output_topic: "/patient/bed_exit"
    voting_window: 3
    voting_threshold: 2
    prefill_safe_queue: true
    frame_rotate_ccw: 90
    tts_enabled: true
    tts_repeat_interval: 0.03
    show: false
""",
        encoding='utf-8',
    )

    rclpy.init(args=['--ros-args', '--params-file', str(params_yaml)])
    node = sub = executor = None
    try:
        node = bed_exit_node.BedExitNode(detector=ensemble)
        node.timer.cancel()
        sub = _Subscriber('/patient/bed_exit')
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        executor.add_node(sub)

        # 1) 1회차 True(1/3) -> 아직 K=2 미달이므로 False 발행, TTS 시작 안 함
        node.process_frame()
        assert _spin_until(executor, lambda: len(sub.received) == 1)
        assert sub.received[-1] is False
        time.sleep(0.05)
        assert len(procs) == 0

        # 2) 2회차 True(2/3) -> 이탈 확정(True), 비동기 TTS 1회차 즉시 시작 & 발행 비차단
        t0 = time.monotonic()
        node.process_frame()
        assert (time.monotonic() - t0) < 0.1
        assert _spin_until(executor, lambda: len(sub.received) == 2)
        assert sub.received[-1] is True
        assert spawn_events[0].wait(timeout=2.0)
        assert procs[0].started.wait(timeout=2.0)
        assert procs[0].argv == ['/usr/bin/spd-say', '-l', 'ko', '-w', DEFAULT_TTS_MESSAGE]

        # 1회차 발화 진행 중에도 ROS 발행이 전혀 막히지 않고 계속 동작해야 한다.
        node.process_frame()
        assert _spin_until(executor, lambda: len(sub.received) == 3)
        assert sub.received[-1] is True

        # 3) 1회차 발화 종료 -> 여전히 이탈 상태이므로 2회차 자동 반복
        procs[0].allow_finish.set()
        assert procs[0].finished.wait(timeout=2.0)
        assert spawn_events[1].wait(timeout=2.0)
        assert procs[1].started.wait(timeout=2.0)

        # 4) 2회차 발화 도중 정상 복귀(False): 현재 발화는 끊지 않고 끝까지 재생 후 반복 중단
        mode['state'] = 'safe'
        node.process_frame()
        node.process_frame()
        assert _spin_until(executor, lambda: len(sub.received) == 5)
        assert sub.received[-1] is False
        time.sleep(0.05)
        assert procs[1].terminated is False
        assert node.tts.is_speaking is True

        procs[1].allow_finish.set()
        assert procs[1].finished.wait(timeout=2.0)
        time.sleep(0.08)
        assert len(procs) == 2  # 3회차 시작되지 않음

        # 5) 재이탈 -> 3회차 발화 도중 예외(JudgmentUnavailable) 발생:
        #    발행 생략 + 큐 초기화 + 현재 3회차 발화는 끝까지 재생 후 추가 반복 없음(무한루프 방지)
        mode['state'] = 'exit'
        node.process_frame()
        node.process_frame()
        assert _spin_until(executor, lambda: len(sub.received) == 7)
        assert sub.received[-1] is True
        assert spawn_events[2].wait(timeout=2.0)
        assert procs[2].started.wait(timeout=2.0)

        mode['state'] = 'unavailable'
        before_count = len(sub.received)
        node.process_frame()
        executor.spin_once(timeout_sec=0.05)
        assert len(sub.received) == before_count  # 예외 프레임 미발행 유지
        assert procs[2].terminated is False

        procs[2].allow_finish.set()
        assert procs[2].finished.wait(timeout=2.0)
        time.sleep(0.08)
        assert len(procs) == 3  # 예외 후 무한 반복 없음
        assert all(shape == (24, 12, 3) for shape in seen_shapes)
    finally:
        if executor is not None:
            executor.shutdown()
        if sub is not None:
            sub.destroy_node()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


@pytest.mark.integration
def test_params_template_yaml_and_json_config_load_cleanly(tmp_path, monkeypatch):
    assert TEMPLATE_YAML.is_file()
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda src: _FrameCapture(frame))

    class _Dummy:
        def judge(self, img):
            return False

    # 1) 배포된 config/bed_exit.params.template.yaml의 모든 키·타입이 ROS 2에 정상 로드됨
    rclpy.init(args=['--ros-args', '--params-file', str(TEMPLATE_YAML)])
    node = None
    try:
        node = bed_exit_node.BedExitNode(detector=_Dummy())
        assert node.voting_window == 5
        assert node.voting_threshold == 3
        assert node.tts.message == DEFAULT_TTS_MESSAGE
        assert node.tts.enabled is True
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    # 2) method_config JSON 하나만으로 투표·TTS·파라미터 전달 동작 확인
    cfg_json = tmp_path / 'single_config.json'
    cfg_json.write_text(
        json.dumps({
            'method': 'segmentation',
            'model_dir': '../models',
            'roi': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            'voting_window': 6,
            'voting_threshold': 4,
            'tts_message': '단일 JSON 설정 안내',
            'tts_repeat_interval': 1.5,
            'show': False,
        }),
        encoding='utf-8',
    )
    rclpy.init(args=['--ros-args', '-p', f'method_config:={cfg_json}'])
    node = None
    try:
        node = bed_exit_node.BedExitNode(detector=_Dummy())
        assert node.voting_window == 6
        assert node.voting_threshold == 4
        assert node.tts.message == '단일 JSON 설정 안내'
        assert node.tts.repeat_interval == 1.5
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
