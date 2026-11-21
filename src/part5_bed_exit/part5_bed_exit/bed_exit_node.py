"""웹캠·MP4 입력부터 ROS 토픽 발행·비동기 TTS 안내까지 운영 파이프라인을 총괄하는 파일.

주요 역할:
- OpenCV로 웹캠 또는 MP4의 BGR 프레임을 주기적으로 읽는다(선택적 90° 단위 회전 지원).
- 설정에서 선택한 판단 객체(단일 모델 또는 선택적 앙상블)의 ``judge(frame)``에 프레임을 전달한다.
- 프레임별 판단을 ``BedExitState``의 N프레임 다수결(기본 5프레임 중 3회 이상)에 넣는다.
- 최종 ``bool``을 ``/patient/bed_exit`` 토픽으로 발행한다.
- N회 중 K회 이상 이탈이 확정(``state.unsafe == True``)되면 비동기 반복 TTS 안내를 시작하고,
  정상 복귀(``False``) 또는 투표 큐 초기화 시 현재 진행 중인 1회 발화가 끝난 뒤 반복을 멈춘다.
- 모든 파라미터는 ``method_config`` JSON 또는 ROS 2 파라미터(``-p`` / ``--params-file``)로
  전달할 수 있으며, 미지정 시 기존 v1.3 기본값을 그대로 유지한다.

``setup.py``의 ``bed_exit`` 명령이 이 파일의 ``main()``을 호출한다.
"""

import json
import math
import os
from pathlib import Path
import time

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_method import (
    load_bed_exit_method,
    validate_bed_exit_method,
)
from part5_bed_exit.bed_exit_state import (
    DEFAULT_VOTING_THRESHOLD,
    DEFAULT_VOTING_WINDOW,
    BedExitState,
)
from part5_bed_exit.pretrained import rotate_frame_ccw, validate_rotation_angle
from part5_bed_exit.tts_speaker import (
    DEFAULT_TTS_LANGUAGE,
    DEFAULT_TTS_MESSAGE,
    DEFAULT_TTS_REPEAT_INTERVAL,
    TtsSpeaker,
)

WINDOW_TITLE = 'Part 5 Bed Exit (/patient/bed_exit)'


def _resolve_default_method_config() -> str:
    """``method_config`` 미지정 시 설치된 또는 소스 트리의 ``methods.default.json``을 찾는다."""
    try:
        from ament_index_python.packages import get_package_share_directory
        installed = Path(get_package_share_directory('part5_bed_exit')) / 'config' / 'methods.default.json'
        if installed.is_file():
            return str(installed)
    except Exception:
        pass
    local = Path(__file__).resolve().parents[1] / 'config' / 'methods.default.json'
    return str(local) if local.is_file() else ''


def _load_config_defaults(config_path: str) -> dict:
    """``method_config`` JSON이 유효하면 노드 파라미터 기본값 참조용 딕셔너리를 읽는다."""
    if not config_path:
        return {}
    try:
        path = Path(config_path).expanduser().resolve()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        # 형식 오류나 경로 오류는 이후 load_bed_exit_method()에서 일관되게 보고한다.
        return {}


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    value = cfg.get(key)
    if value is None:
        return default
    if type(value) is bool or not isinstance(value, int):
        raise ValueError(f'{key} in method_config must be an int')
    return value


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    value = cfg.get(key)
    if value is None:
        return default
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f'{key} in method_config must be a float')
    return float(value)


def _cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key)
    if value is None:
        return default
    if type(value) is not bool:
        raise ValueError(f'{key} in method_config must be a bool')
    return value


def _cfg_str(cfg: dict, key: str, default: str) -> str:
    value = cfg.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f'{key} in method_config must be a string')
    return value


class BedExitNode(Node):
    """웹캠·MP4 영상으로 침대 이탈을 판단하여 ROS 2 Bool 토픽과 비동기 TTS를 구동하는 노드."""

    def __init__(self, detector=None, video_path: str | None = None, tts=None):
        """ROS 파라미터(및 ``method_config`` JSON)·판단 객체·투표·TTS·카메라·타이머를 초기화한다."""
        super().__init__('bed_exit')
        # 초기화 도중 예외가 나더라도 destroy_node()/예외 정리가 안전하도록 먼저 기본값으로 둔다.
        self.capture = None
        self.detector = None
        self.tts = None
        self.show = False
        self.window_title = WINDOW_TITLE
        self.is_video_file = False
        self.loop_video = False
        self.camera_index = None
        self.reconnect_interval = 1.0
        self._last_reconnect_attempt = None
        self.frame_rotate_ccw = 0
        self.voting_window = DEFAULT_VOTING_WINDOW
        self.voting_threshold = DEFAULT_VOTING_THRESHOLD
        self.prefill_safe_queue = False
        self.publisher_qos_depth = 10

        # 1) method / method_config를 먼저 읽어 JSON의 선택적 노드 파라미터를 기본값으로 반영한다.
        #    우선순위: ROS CLI/YAML 파라미터 > method_config JSON > v1.3 기본값
        self.declare_parameter('method', '')
        self.declare_parameter('method_config', '')
        method_path = self.get_parameter('method').value
        config_path = self.get_parameter('method_config').value
        if detector is None and not config_path and (not method_path or ':' not in str(method_path)):
            config_path = _resolve_default_method_config()
        cfg = _load_config_defaults(config_path)

        # 2) 기본 실행·입출력·회전 파라미터
        self.declare_parameter('output_topic', _cfg_str(cfg, 'output_topic', '/patient/bed_exit'))
        self.declare_parameter('publisher_qos_depth', _cfg_int(cfg, 'publisher_qos_depth', 10))
        self.declare_parameter('camera_index', _cfg_int(cfg, 'camera_index', 0))
        self.declare_parameter('reconnect_interval', _cfg_float(cfg, 'reconnect_interval', 1.0))
        default_video = (
            str(video_path) if video_path is not None else _cfg_str(cfg, 'video_path', '')
        )
        self.declare_parameter('video_path', default_video)
        self.declare_parameter('loop_video', _cfg_bool(cfg, 'loop_video', False))
        self.declare_parameter('capture_fps', _cfg_float(cfg, 'capture_fps', 30.0))
        self.declare_parameter('frame_rotate_ccw', _cfg_int(cfg, 'frame_rotate_ccw', 0))
        self.declare_parameter('roi_already_rotated', _cfg_bool(cfg, 'roi_already_rotated', False))
        default_show = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
        self.declare_parameter('show', _cfg_bool(cfg, 'show', default_show))
        self.declare_parameter(
            'preview_window_title', _cfg_str(cfg, 'preview_window_title', WINDOW_TITLE)
        )

        # 3) N프레임 다수결 투표 파라미터
        self.declare_parameter(
            'voting_window', _cfg_int(cfg, 'voting_window', DEFAULT_VOTING_WINDOW)
        )
        self.declare_parameter(
            'voting_threshold', _cfg_int(cfg, 'voting_threshold', DEFAULT_VOTING_THRESHOLD)
        )
        self.declare_parameter('prefill_safe_queue', _cfg_bool(cfg, 'prefill_safe_queue', False))

        # 4) 비동기 반복 TTS 파라미터
        self.declare_parameter('tts_enabled', _cfg_bool(cfg, 'tts_enabled', True))
        self.declare_parameter('tts_message', _cfg_str(cfg, 'tts_message', DEFAULT_TTS_MESSAGE))
        self.declare_parameter('tts_language', _cfg_str(cfg, 'tts_language', DEFAULT_TTS_LANGUAGE))
        self.declare_parameter(
            'tts_repeat_interval',
            _cfg_float(cfg, 'tts_repeat_interval', DEFAULT_TTS_REPEAT_INTERVAL),
        )
        self.declare_parameter('tts_command', _cfg_str(cfg, 'tts_command', ''))
        self.declare_parameter('tts_audio_file', _cfg_str(cfg, 'tts_audio_file', ''))

        # 5) 내장 모델·선택적 앙상블 오버라이드 파라미터 (센티널 유지 시 JSON/v1.3 기본값 사용)
        self.declare_parameter('weights', '')
        self.declare_parameter('device', '')
        self.declare_parameter('yolo_confidence', -1.0)
        self.declare_parameter('yolo_imgsz', 0)
        self.declare_parameter('outside_fraction', -1.0)
        self.declare_parameter('mask_threshold', -1.0)
        self.declare_parameter('selector_init_overlap', -1.0)
        self.declare_parameter('selector_min_iou', -1.0)
        self.declare_parameter('selector_ambiguity_gap', -1.0)
        self.declare_parameter('pose_policy', '')
        self.declare_parameter('pose_point_conf', -1.0)
        self.declare_parameter('pose_tolerance_px', -1.0)
        self.declare_parameter('mediapipe_num_poses', 0)
        self.declare_parameter('mediapipe_min_detection_conf', -1.0)
        self.declare_parameter('mediapipe_min_presence_conf', -1.0)
        self.declare_parameter('ensemble_methods', '')
        self.declare_parameter('ensemble_policy', '')
        self.declare_parameter('ensemble_min_votes', 0)
        self.declare_parameter('ensemble_require_all_valid', False)

        # 파라미터 값 조회 및 검증
        topic = self.get_parameter('output_topic').value
        qos_depth = self.get_parameter('publisher_qos_depth').value
        camera_index = self.get_parameter('camera_index').value
        reconnect_interval = float(self.get_parameter('reconnect_interval').value)
        resolved_video = str(self.get_parameter('video_path').value).strip()
        self.loop_video = bool(self.get_parameter('loop_video').value)
        fps = self.get_parameter('capture_fps').value
        raw_rotate_ccw = self.get_parameter('frame_rotate_ccw').value
        roi_already_rotated = bool(self.get_parameter('roi_already_rotated').value)
        self.show = bool(self.get_parameter('show').value)
        self.window_title = str(self.get_parameter('preview_window_title').value) or WINDOW_TITLE

        voting_window = self.get_parameter('voting_window').value
        voting_threshold = self.get_parameter('voting_threshold').value
        prefill_safe_queue = bool(self.get_parameter('prefill_safe_queue').value)

        tts_enabled = bool(self.get_parameter('tts_enabled').value)
        tts_message = str(self.get_parameter('tts_message').value)
        tts_language = str(self.get_parameter('tts_language').value)
        tts_repeat_interval = float(self.get_parameter('tts_repeat_interval').value)
        tts_command = str(self.get_parameter('tts_command').value)
        tts_audio_file = str(self.get_parameter('tts_audio_file').value)

        if not topic or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Set output_topic and capture_fps > 0')
        if type(qos_depth) is not int or qos_depth < 1:
            raise ValueError('Set publisher_qos_depth >= 1')
        if not math.isfinite(reconnect_interval) or reconnect_interval <= 0:
            raise ValueError('Set reconnect_interval > 0')
        self.frame_rotate_ccw = validate_rotation_angle(raw_rotate_ccw)
        self.publisher_qos_depth = qos_depth

        # 무거운 모델 가중치 로딩 전에 투표·TTS 파라미터를 먼저 검증한다.
        initial_state = BedExitState(
            window_size=voting_window,
            threshold=voting_threshold,
            prefill_safe=prefill_safe_queue,
        )
        self.voting_window = initial_state.window_size
        self.voting_threshold = initial_state.threshold
        self.prefill_safe_queue = initial_state.prefill_safe

        resolved_tts = (
            tts
            if tts is not None
            else TtsSpeaker(
                message=tts_message,
                enabled=tts_enabled,
                repeat_interval=tts_repeat_interval,
                language=tts_language,
                command=tts_command or None,
                audio_file=tts_audio_file or None,
                on_error=lambda msg: BedExitNode._log_callback_error(self, msg),
            )
        )

        if detector is None and not method_path and not config_path:
            raise ValueError('Set method or method_config')

        model_overrides = self._collect_model_overrides(
            config_path=config_path,
            frame_rotate_ccw=self.frame_rotate_ccw,
            roi_already_rotated=roi_already_rotated,
        )

        if detector is None:
            if model_overrides:
                self.detector = load_bed_exit_method(
                    method_path, config_path, overrides=model_overrides
                )
            else:
                self.detector = load_bed_exit_method(method_path, config_path)
        else:
            self.detector = validate_bed_exit_method(detector)

        try:
            self.state = initial_state
            self.tts = resolved_tts
            self.publisher = self.create_publisher(Bool, topic, self.publisher_qos_depth)
            self.is_video_file = bool(resolved_video)
            self.reconnect_interval = reconnect_interval
            if self.is_video_file:
                source_desc = f'video_path={resolved_video}'
                self.capture = cv2.VideoCapture(resolved_video)
                if not self.capture.isOpened():
                    raise RuntimeError(f'Cannot open video source ({source_desc})')
            else:
                self.camera_index = camera_index
                source_desc = f'camera_index={camera_index}'
                self._open_camera(initial=True)
            self.timer = self.create_timer(1.0 / fps, self.process_frame)
            self.get_logger().info(
                f'BedExitNode started: {source_desc}, topic={topic}, '
                f'method={type(self.detector).__name__}, show={self.show}, '
                f'loop_video={self.loop_video}, camera_connected={self.capture is not None}, '
                f'vote={self.voting_threshold}/{self.voting_window}, '
                f'prefill_safe={self.prefill_safe_queue}, '
                f'rotate_ccw={self.frame_rotate_ccw}, '
                f'tts_enabled={bool(getattr(self.tts, "enabled", False))}'
            )
        except Exception:
            self._close_tts()
            self._release_capture()
            self._close_detector()
            raise

    def _collect_model_overrides(
        self,
        *,
        config_path: str,
        frame_rotate_ccw: int,
        roi_already_rotated: bool,
    ) -> dict:
        """센티널이 아닌 ROS 모델·앙상블 파라미터만 추려 ``overrides`` 딕셔너리를 만든다."""
        overrides: dict = {}

        for name in ('weights', 'device', 'pose_policy', 'ensemble_methods', 'ensemble_policy'):
            value = str(self.get_parameter(name).value).strip()
            if value:
                overrides[name] = value

        for name in (
            'yolo_confidence',
            'outside_fraction',
            'mask_threshold',
            'selector_init_overlap',
            'selector_min_iou',
            'selector_ambiguity_gap',
            'pose_point_conf',
            'pose_tolerance_px',
            'mediapipe_min_detection_conf',
            'mediapipe_min_presence_conf',
        ):
            value = float(self.get_parameter(name).value)
            if value != -1.0:
                overrides[name] = value

        for name in ('yolo_imgsz', 'mediapipe_num_poses', 'ensemble_min_votes'):
            value = self.get_parameter(name).value
            if value != 0:
                overrides[name] = value

        if bool(self.get_parameter('ensemble_require_all_valid').value):
            overrides['ensemble_require_all_valid'] = True

        if frame_rotate_ccw != 0 and config_path:
            overrides['frame_rotate_ccw'] = frame_rotate_ccw
            if roi_already_rotated:
                overrides['roi_already_rotated'] = True

        return overrides

    def _sync_tts(self):
        """현재 투표 확정 상태(``state.unsafe``)에 맞춰 비동기 TTS 활성 여부를 동기화한다."""
        tts = getattr(self, 'tts', None)
        set_active = getattr(tts, 'set_active', None)
        if not callable(set_active):
            return
        active = bool(getattr(getattr(self, 'state', None), 'unsafe', False))
        try:
            set_active(active)
        except Exception as exc:
            BedExitNode._log_callback_error(
                self,
                f'TTS notification failed; continuing without interrupting publish: {exc}',
            )

    def _reset_state(self):
        """투표 큐를 초기화(``unsafe=False``)하고 TTS 무한 반복을 방지하기 위해 동기화한다."""
        state = getattr(self, 'state', None)
        reset = getattr(state, 'reset', None)
        if callable(reset):
            reset()
        else:
            self.state = BedExitState(
                window_size=getattr(self, 'voting_window', DEFAULT_VOTING_WINDOW),
                threshold=getattr(self, 'voting_threshold', DEFAULT_VOTING_THRESHOLD),
                prefill_safe=getattr(self, 'prefill_safe_queue', False),
            )
        BedExitNode._sync_tts(self)

    def _render_preview(self, frame, voted_unsafe):
        """현재 프레임과 모델 시각화 위에 N프레임 투표·TTS 상태를 표시한다."""
        if not self.show:
            return
        render = getattr(self.detector, 'render', None)

        canvas = render(frame) if callable(render) else frame.copy()
        h, w = canvas.shape[:2]

        win_size = getattr(self.state, 'window_size', DEFAULT_VOTING_WINDOW)
        true_count = sum(self.state.history)
        win_len = len(self.state.history)

        tts = getattr(self, 'tts', None)
        tts_tag = ''
        if tts is not None and (
            getattr(tts, 'is_speaking', False) or getattr(tts, 'is_active', False)
        ):
            tts_tag = ' | TTS'

        if voted_unsafe is None:
            vote_text = f'{win_size}-frame vote: RESET ({true_count}/{win_len}){tts_tag}'
            color = (0, 165, 255)
        elif voted_unsafe:
            vote_text = f'{win_size}-frame vote: UNSAFE ({true_count}/{win_len}) -> True{tts_tag}'
            color = (0, 0, 255)
        else:
            vote_text = f'{win_size}-frame vote: SAFE ({true_count}/{win_len}) -> False{tts_tag}'
            color = (0, 220, 0)

        cv2.rectangle(canvas, (0, h - 36), (min(w, 520), h), (20, 20, 20), -1)
        cv2.putText(
            canvas,
            vote_text,
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
        try:
            cv2.imshow(getattr(self, 'window_title', WINDOW_TITLE), canvas)
            cv2.waitKey(1)
        except cv2.error:
            self.show = False

    def _log_callback_error(self, message):
        try:
            self.get_logger().error(message)
        except Exception:
            pass

    def _log_callback_info(self, message):
        try:
            self.get_logger().info(message)
        except Exception:
            pass

    def _close_capture_handle(self):
        capture = getattr(self, 'capture', None)
        if capture is not None:
            release = getattr(capture, 'release', None)
            if callable(release):
                try:
                    release()
                except Exception:
                    pass
            self.capture = None

    def _disconnect_camera(self):
        BedExitNode._close_capture_handle(self)
        self._last_reconnect_attempt = time.monotonic()

    def _open_camera(self, initial: bool = False) -> bool:
        camera_index = getattr(self, 'camera_index', None)
        if camera_index is None:
            return False
        BedExitNode._close_capture_handle(self)
        self._last_reconnect_attempt = time.monotonic()
        try:
            capture = cv2.VideoCapture(camera_index)
            is_opened = capture is not None and bool(capture.isOpened())
        except Exception as exc:
            BedExitNode._log_callback_error(
                self,
                f'Webcam open failed (camera_index={camera_index}); '
                f'waiting to reconnect: {exc}',
            )
            return False

        if not is_opened:
            release = getattr(capture, 'release', None)
            if callable(release):
                try:
                    release()
                except Exception:
                    pass
            interval = float(getattr(self, 'reconnect_interval', 1.0))
            BedExitNode._log_callback_error(
                self,
                f'Webcam unavailable (camera_index={camera_index}); '
                f'retrying every {interval:.1f}s without stopping node',
            )
            return False

        self.capture = capture
        if not initial:
            BedExitNode._log_callback_info(
                self, f'Webcam reconnected (camera_index={camera_index})')
        return True

    def _ensure_camera(self) -> bool:
        capture = getattr(self, 'capture', None)
        if capture is not None:
            is_opened = getattr(capture, 'isOpened', None)
            if not callable(is_opened):
                return True
            try:
                if is_opened():
                    return True
            except Exception:
                pass
            BedExitNode._disconnect_camera(self)

        if getattr(self, 'camera_index', None) is None:
            return False

        interval = float(getattr(self, 'reconnect_interval', 1.0))
        last_attempt = getattr(self, '_last_reconnect_attempt', None)
        if last_attempt is not None and (time.monotonic() - last_attempt) < interval:
            return False
        return BedExitNode._open_camera(self, initial=False)

    def _render_preview_safely(self, frame, voted_unsafe):
        render_preview = getattr(self, '_render_preview', None)
        if not callable(render_preview):
            return
        try:
            render_preview(frame, voted_unsafe)
        except Exception as exc:
            self.show = False
            BedExitNode._log_callback_error(
                self, f'Preview disabled after rendering failure: {exc}')

    def process_frame(self):
        """타이머마다 입력 읽기 → 판정 → N프레임 다수결 → ROS 발행 → TTS 동기화를 수행한다."""
        is_video_file = bool(getattr(self, 'is_video_file', False))
        if not is_video_file and not BedExitNode._ensure_camera(self):
            BedExitNode._reset_state(self)
            return

        try:
            ok, frame = self.capture.read()
        except Exception as exc:
            BedExitNode._log_callback_error(
                self, f'Video source read failed; no frame decision published: {exc}')
            BedExitNode._reset_state(self)
            if not is_video_file and getattr(self, 'camera_index', None) is not None:
                BedExitNode._disconnect_camera(self)
            return

        loop_video = bool(getattr(self, 'loop_video', False))
        if (not ok or frame is None) and is_video_file and loop_video:
            BedExitNode._reset_state(self)
            try:
                if not self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0):
                    raise RuntimeError('capture does not support seeking to frame 0')
                reset = getattr(self.detector, 'reset', None)
                if callable(reset):
                    reset()
                ok, frame = self.capture.read()
                if not ok or frame is None:
                    raise RuntimeError('first frame could not be read after rewind')
                try:
                    self.get_logger().info('Video playback loop restarted from frame 0')
                except Exception:
                    pass
            except Exception as exc:
                BedExitNode._log_callback_error(
                    self, f'Video loop restart failed; stopping timer: {exc}')
                try:
                    self.timer.cancel()
                except Exception as cancel_exc:
                    BedExitNode._log_callback_error(
                        self, f'Failed to stop video timer: {cancel_exc}')
                return

        if not ok or frame is None:
            BedExitNode._reset_state(self)
            if is_video_file:
                try:
                    self.timer.cancel()
                except Exception as exc:
                    BedExitNode._log_callback_error(
                        self, f'Failed to stop completed video timer: {exc}')
                try:
                    self.get_logger().info('Video playback finished; stopping timer')
                except Exception:
                    pass
            else:
                BedExitNode._log_callback_error(
                    self, 'Webcam read failed; no frame decision published')
                if getattr(self, 'camera_index', None) is not None:
                    BedExitNode._disconnect_camera(self)
            return

        frame_rotate_ccw = getattr(self, 'frame_rotate_ccw', 0)
        if frame_rotate_ccw:
            try:
                frame = rotate_frame_ccw(frame, frame_rotate_ccw)
            except Exception as exc:
                BedExitNode._reset_state(self)
                BedExitNode._log_callback_error(
                    self, f'Frame rotation failed; history reset: {exc}')
                return

        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            BedExitNode._reset_state(self)
            BedExitNode._log_callback_error(
                self, f'Judgment failed; history reset: {exc}')
            BedExitNode._render_preview_safely(self, frame, None)
            return

        try:
            self.publisher.publish(Bool(data=unsafe))
        except Exception as exc:
            BedExitNode._reset_state(self)
            BedExitNode._log_callback_error(
                self, f'Publish failed; history reset and no decision published: {exc}')
            BedExitNode._render_preview_safely(self, frame, None)
            return

        # N회 중 K회 이상 확정된 state.unsafe 값에 맞춰 TTS 활성 상태를 동기화한다.
        BedExitNode._sync_tts(self)
        BedExitNode._render_preview_safely(self, frame, unsafe)

    def _release_capture(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

    def _close_detector(self):
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()
        self.detector = None

    def _close_tts(self):
        tts = getattr(self, 'tts', None)
        close = getattr(tts, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self.tts = None

    def destroy_node(self):
        self._close_tts()
        self._release_capture()
        self._close_detector()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    cli_args = rclpy.utilities.remove_ros_args(args=args)[1:]
    cli_video = cli_args[0] if cli_args else None
    node = None
    try:
        node = BedExitNode(video_path=cli_video)
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
