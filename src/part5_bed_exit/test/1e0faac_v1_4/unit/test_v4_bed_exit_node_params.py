"""목적: v1.4 ``BedExitNode``의 JSON/YAML 파라미터화·프레임 회전·TTS 동기화를 검증한다."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
import rclpy

from part5_bed_exit import bed_exit_node
from part5_bed_exit.bed_exit_state import BedExitState


class _Capture:
    def __init__(self, frame=None):
        self.frame = frame

    def isOpened(self):
        return True

    def read(self):
        return True, self.frame

    def release(self):
        pass


class _Method:
    def __init__(self, decision=False):
        self.decision = decision

    def judge(self, frame):
        return self.decision

    def close(self):
        pass


class _TtsStub:
    def __init__(self, fail_on_set=False):
        self.enabled = True
        self.events = []
        self.closed = False
        self.fail_on_set = fail_on_set

    def set_active(self, active):
        if self.fail_on_set:
            raise RuntimeError('audio bus down')
        self.events.append(bool(active))

    def close(self):
        self.closed = True


def _start_ros(args=None):
    if not rclpy.ok():
        rclpy.init(args=args)


def _stop_ros():
    if rclpy.ok():
        rclpy.shutdown()


def test_init_reads_node_params_from_method_config_json_and_ros_overrides(tmp_path, monkeypatch):
    cfg_path = tmp_path / 'custom_methods.json'
    cfg_path.write_text(
        json.dumps({
            'method': 'segmentation',
            'model_dir': '../models',
            'roi': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            'voting_window': 7,
            'voting_threshold': 4,
            'prefill_safe_queue': True,
            'frame_rotate_ccw': 90,
            'tts_enabled': True,
            'tts_message': 'JSON 안내 문구',
            'tts_repeat_interval': 2.5,
            'capture_fps': 15.0,
        }),
        encoding='utf-8',
    )
    loader_calls = []
    detector = _Method()
    monkeypatch.setattr(
        bed_exit_node,
        'load_bed_exit_method',
        lambda method, config, overrides=None: loader_calls.append(
            (method, config, overrides)
        ) or detector,
    )
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: _Capture())

    _start_ros([
        '--ros-args',
        '-p', f'method_config:={cfg_path}',
        '-p', 'voting_threshold:=5',
        '-p', 'show:=false',
    ])
    node = None
    try:
        node = bed_exit_node.BedExitNode()
        assert node.voting_window == 7
        assert node.voting_threshold == 5
        assert node.prefill_safe_queue is True
        assert node.frame_rotate_ccw == 90
        assert node.tts.message == 'JSON 안내 문구'
        assert node.tts.repeat_interval == 2.5
        assert len(node.state.history) == 7
        assert loader_calls == [('', str(cfg_path), {'frame_rotate_ccw': 90})]
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


def test_init_applies_yaml_params_file_and_passes_model_and_ensemble_overrides(tmp_path, monkeypatch):
    yaml_path = tmp_path / 'params.yaml'
    yaml_path.write_text(
        """bed_exit:
  ros__parameters:
    method: "ensemble"
    method_config: "/tmp/dummy.json"
    output_topic: "/custom/bed_exit"
    publisher_qos_depth: 5
    capture_fps: 20.0
    frame_rotate_ccw: 270
    voting_window: 4
    voting_threshold: 2
    prefill_safe_queue: true
    tts_enabled: true
    tts_message: "YAML 경고"
    tts_repeat_interval: 0.5
    show: false
    weights: "ft2_best.pt"
    device: "cuda:0"
    yolo_confidence: 0.35
    yolo_imgsz: 512
    outside_fraction: 0.08
    mask_threshold: 0.6
    selector_init_overlap: 0.4
    selector_min_iou: 0.2
    selector_ambiguity_gap: 0.1
    ensemble_methods: "segmentation,yolo_pose"
    ensemble_policy: "any"
    ensemble_min_votes: 1
    ensemble_require_all_valid: true
""",
        encoding='utf-8',
    )
    loader_calls = []
    detector = _Method()
    monkeypatch.setattr(
        bed_exit_node,
        'load_bed_exit_method',
        lambda method, config, overrides=None: loader_calls.append(
            (method, config, overrides)
        ) or detector,
    )
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: _Capture())

    _start_ros(['--ros-args', '--params-file', str(yaml_path)])
    node = None
    try:
        node = bed_exit_node.BedExitNode()
        assert node.publisher_qos_depth == 5
        assert node.voting_window == 4
        assert node.voting_threshold == 2
        assert node.prefill_safe_queue is True
        assert node.frame_rotate_ccw == 270
        assert node.tts.message == 'YAML 경고'
        assert node.tts.repeat_interval == 0.5
        assert loader_calls == [
            (
                'ensemble',
                '/tmp/dummy.json',
                {
                    'weights': 'ft2_best.pt',
                    'device': 'cuda:0',
                    'yolo_confidence': 0.35,
                    'yolo_imgsz': 512,
                    'outside_fraction': 0.08,
                    'mask_threshold': 0.6,
                    'selector_init_overlap': 0.4,
                    'selector_min_iou': 0.2,
                    'selector_ambiguity_gap': 0.1,
                    'frame_rotate_ccw': 270,
                    'ensemble_methods': 'segmentation,yolo_pose',
                    'ensemble_policy': 'any',
                    'ensemble_min_votes': 1,
                    'ensemble_require_all_valid': True,
                },
            )
        ]
    finally:
        if node is not None:
            node.destroy_node()
        _stop_ros()


@pytest.mark.parametrize(
    'bad_args',
    [
        ['-p', 'voting_window:=3', '-p', 'voting_threshold:=4'],
        ['-p', 'publisher_qos_depth:=0'],
        ['-p', 'frame_rotate_ccw:=45'],
        ['-p', 'tts_repeat_interval:=-1.0'],
    ],
)
def test_init_rejects_invalid_new_parameters(monkeypatch, bad_args):
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda source: _Capture())
    _start_ros(['--ros-args', '-p', 'show:=false', *bad_args])
    try:
        with pytest.raises(ValueError):
            bed_exit_node.BedExitNode(detector=_Method())
    finally:
        _stop_ros()


def test_process_frame_rotates_frame_and_syncs_tts_on_vote_and_reset():
    raw_frame = np.zeros((4, 8, 3), dtype=np.uint8)
    seen_shapes = []
    decisions = iter([True, True, False, False, RuntimeError('no_person')])

    class _ScriptedDetector:
        def judge(self, frame):
            seen_shapes.append(frame.shape)
            val = next(decisions)
            if isinstance(val, Exception):
                raise val
            return val

    published = []
    tts = _TtsStub()
    node = SimpleNamespace(
        capture=_Capture(raw_frame),
        detector=_ScriptedDetector(),
        state=BedExitState(window_size=3, threshold=2, prefill_safe=True),
        tts=tts,
        frame_rotate_ccw=90,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=lambda msg: None),
    )

    bed_exit_node.BedExitNode.process_frame(node)
    bed_exit_node.BedExitNode.process_frame(node)
    bed_exit_node.BedExitNode.process_frame(node)
    bed_exit_node.BedExitNode.process_frame(node)
    bed_exit_node.BedExitNode.process_frame(node)

    assert all(shape == (8, 4, 3) for shape in seen_shapes)
    assert published == [False, True, True, False]
    assert tts.events == [False, True, True, False, False]
    assert list(node.state.history) == [False, False, False]


def test_process_frame_isolates_tts_failure_and_destroy_closes_tts():
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    published = []
    errors = []
    tts = _TtsStub(fail_on_set=True)
    node = SimpleNamespace(
        capture=_Capture(frame),
        detector=_Method(True),
        state=BedExitState(window_size=1, threshold=1),
        tts=tts,
        publisher=SimpleNamespace(publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(error=errors.append),
    )

    bed_exit_node.BedExitNode.process_frame(node)
    assert published == [True]
    assert any('TTS notification failed' in err for err in errors)

    bed_exit_node.BedExitNode._close_tts(node)
    assert tts.closed is True
    assert node.tts is None
