"""웹캠·MP4 입력부터 ROS 토픽 발행까지 운영 파이프라인을 총괄하는 파일.

주요 역할:
- OpenCV로 웹캠 또는 MP4의 BGR 프레임을 주기적으로 읽는다.
- 설정에서 선택한 판단 객체의 ``judge(frame)``에 프레임을 전달한다.
- 프레임별 판단을 ``BedExitState``의 5프레임 다수결에 넣는다.
- 최종 ``bool``을 ``/patient/bed_exit`` 토픽으로 발행한다.
- 카메라나 판단 메서드가 실패하면 투표 이력을 초기화하고 발행을 생략한다.

``setup.py``의 ``bed_exit`` 명령이 이 파일의 ``main()``을 호출한다.

System Flowchart v5 대응:
``process_frame()``이 이미지 입력부터 프레임 판정, 5프레임 다수결, ROS 발행까지
Part 5 전체 흐름을 연결한다. 사람 인식과 ROI 판정은 판단 객체의 ``judge()``에,
다수결은 ``BedExitState.update()``에 위임한다.

코드 파도타기:
``setup.py:entry_points`` → ``main()`` → ``BedExitNode.__init__()`` →
``process_frame()`` → ``bed_exit_core.BedExitMethod.judge()`` →
``bed_exit_state.BedExitState.update()`` → ``publisher.publish()``.
"""

import math
import os
import time

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_method import (
    load_bed_exit_method,
    validate_bed_exit_method,
)
from part5_bed_exit.bed_exit_state import BedExitState

WINDOW_TITLE = 'Part 5 Bed Exit (/patient/bed_exit)'


# [PART05-V5 노드 정보]
# 노드 이름: bed_exit
# 입력: camera_index로 지정한 USB 웹캠 또는 video_path의 MP4 BGR 프레임
# 설정: method의 내장 이름 또는 module:ClassOrObject, method_config의 내장 모델 설정
# 처리: 동적으로 선택한 판단 메서드 → 최근 5프레임 중 3회 이상 이탈 다수결
# 출력: output_topic의 std_msgs/msg/Bool (True=침대 이탈, False=정상)
# 실행 주기: capture_fps를 이용해 만든 ROS 타이머 주기
# 실패 정책: 카메라·추론 실패 시 투표 이력을 초기화하고 해당 프레임은 발행하지 않음
# 선택 기능: show=True이면 ROI·마스크·투표 상태를 OpenCV 창으로 표시
class BedExitNode(Node):
    """웹캠·MP4 영상으로 침대 이탈을 판단하여 ROS 2 Bool 토픽으로 발행하는 노드.

    이 클래스는 모델의 세부 추론을 직접 구현하지 않는다. 웹캠 프레임을
    ``detector.judge(frame)``에 전달하고, 반환된 프레임별 판단을
    ``BedExitState.update()``에 누적한 뒤 최종 다수결 결과만 발행한다.

    테스트에서는 ``detector`` 인자로 가짜 판단 객체를 주입할 수 있다. 운영에서는
    ``method``의 동적 대상 또는 ``method_config``의 내장 방식을 읽어 판단 객체를 생성한다.
    """

    def __init__(self, detector=None, video_path: str | None = None):
        """ROS 파라미터·판단 객체·카메라·발행자·주기 타이머를 초기화한다.

        ``detector``를 전달하면 테스트 대역을 사용하고, 전달하지 않으면 검수된
        ``method``와 ``method_config``로 선택된 판단 객체를 생성한다. ``video_path``가
        있으면 웹캠 대신 MP4 파일을 입력으로 사용한다.
        """
        # [PART05-V5-02 노드 초기화]
        # 이전: setup.py:entry_points → bed_exit_node.main()
        # 다음: bed_exit_method.load_bed_exit_method() → cv2.VideoCapture → process_frame()
        super().__init__('bed_exit')
        # 초기화 도중 예외가 나더라도 destroy_node()/예외 정리가 안전하도록 먼저 None으로 둔다.
        self.capture = None
        self.detector = None
        self.show = False
        self.is_video_file = False
        self.loop_video = False
        self.camera_index = None
        self.reconnect_interval = 1.0
        self._last_reconnect_attempt = None

        # method(str, 기본값=''): 빈 값이면 method_config JSON의 method를 사용한다.
        # 내장 이름 또는 package.module:ClassOrObject 형식으로 실행 구현을 선택할 수 있다.
        self.declare_parameter('method', '')
        # method_config(str, 기본값=''): 모델 경로·추론 장치·고정 Bed ROI가 든 JSON 경로.
        # 내장 모델 또는 JSON의 method를 쓰는 운영 실행에서는 빈 값이 허용되지 않는다.
        self.declare_parameter('method_config', '')
        # output_topic(str, 기본값='/patient/bed_exit'): 최종 Bool 판단을 발행할 ROS 토픽 이름.
        self.declare_parameter('output_topic', '/patient/bed_exit')
        # camera_index(int, 기본값=0): OpenCV VideoCapture가 열 USB 카메라 장치 번호.
        self.declare_parameter('camera_index', 0)
        # reconnect_interval(float, 기본값=1.0): 웹캠 미연결·끊김 시 재연결 시도 최소 간격(초).
        self.declare_parameter('reconnect_interval', 1.0)
        # video_path(str, 기본값=''): 값이 있으면 웹캠 대신 해당 MP4를 입력으로 사용한다.
        self.declare_parameter('video_path', '' if video_path is None else str(video_path))
        # loop_video(bool, 기본값=False): MP4 EOF에서 첫 프레임으로 되감아 반복할지 선택한다.
        # 카메라 입력에서는 이 값을 사용하지 않는다.
        self.declare_parameter('loop_video', False)
        # capture_fps(float, 기본값=30.0): process_frame() 호출을 시도하는 초당 타이머 횟수.
        # 실제 처리 FPS는 카메라 읽기와 모델 추론 시간보다 빨라질 수 없다.
        self.declare_parameter('capture_fps', 30.0)
        # show(bool, 기본값=GUI 환경 존재 여부): OpenCV 실시간 판정 프리뷰 표시 여부.
        # DISPLAY나 WAYLAND_DISPLAY가 없으면 헤드리스 환경으로 보고 기본값을 False로 둔다.
        self.declare_parameter(
            'show',
            bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
        )

        # 선언한 ROS 파라미터를 한 번 읽어 이후 초기화와 타이머 주기에 사용한다.
        # output_topic 값을 읽어 create_publisher()의 발행 대상 이름으로 사용한다.
        topic = self.get_parameter('output_topic').value
        # camera_index 값을 읽어 cv2.VideoCapture()에 전달한다.
        camera_index = self.get_parameter('camera_index').value
        # reconnect_interval 값을 읽어 웹캠 재연결 쿨다운(초)으로 사용한다.
        reconnect_interval = float(self.get_parameter('reconnect_interval').value)
        # video_path와 loop_video 값을 읽어 웹캠/영상 입력 및 EOF 정책을 선택한다.
        resolved_video = str(self.get_parameter('video_path').value).strip()
        self.loop_video = bool(self.get_parameter('loop_video').value)
        # capture_fps 값을 읽어 타이머 주기 1.0 / fps를 계산한다.
        fps = self.get_parameter('capture_fps').value
        # show 값을 명시적으로 bool로 변환해 프리뷰 분기 조건으로 사용한다.
        self.show = bool(self.get_parameter('show').value)
        # method와 method_config 값을 읽어 동적 로더에 전달한다.
        method_path = self.get_parameter('method').value
        config_path = self.get_parameter('method_config').value

        # 실제 모델을 생성할 때는 검수된 ROI 설정이 반드시 필요하다.
        if not topic or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Set output_topic and capture_fps > 0')
        if not math.isfinite(reconnect_interval) or reconnect_interval <= 0:
            raise ValueError('Set reconnect_interval > 0')
        if detector is None and not method_path and not config_path:
            raise ValueError('Set method or method_config')

        # 운영 경로: 내장 이름·JSON 선택 또는 module:ClassOrObject 대상을 동적으로 생성한다.
        if detector is None:
            self.detector = load_bed_exit_method(method_path, config_path)
        # 테스트 경로: 가짜 객체가 judge(frame)를 제공하는지만 확인하고 그대로 사용한다.
        else:
            self.detector = validate_bed_exit_method(detector)

        # detector 생성 이후 카메라/영상·타이머 초기화에 실패하면 확보한 리소스를 즉시 정리한다.
        try:
            self.state = BedExitState()
            self.publisher = self.create_publisher(Bool, topic, 10)
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
                # 웹캠 모드에서는 시작 시점에 카메라가 미연결이어도 노드를 종료하지 않고
                # 타이머 주기에서 reconnect_interval마다 재연결을 시도한다.
                self._open_camera(initial=True)
            self.timer = self.create_timer(1.0 / fps, self.process_frame)
            self.get_logger().info(
                f'BedExitNode started: {source_desc}, topic={topic}, '
                f'method={type(self.detector).__name__}, show={self.show}, '
                f'loop_video={self.loop_video}, camera_connected={self.capture is not None}'
            )
        except Exception:
            self._release_capture()
            self._close_detector()
            raise

    def _render_preview(self, frame, voted_unsafe):
        """현재 프레임과 모델 시각화 위에 5프레임 투표 상태를 표시한다."""
        if not self.show:
            return
        render = getattr(self.detector, 'render', None)

        # 메서드가 고유 시각화를 제공하면 사용하고, 없으면 원본 프레임을 복사한다.
        canvas = render(frame) if callable(render) else frame.copy()
        h, w = canvas.shape[:2]

        # 현재 5프레임 창에 쌓인 True 개수와 유효 프레임 수를 표시용으로 계산한다.
        true_count = sum(self.state.history)
        win_len = len(self.state.history)

        # 판단 실패·이탈·정상 상태를 서로 다른 문구와 색으로 구분한다.
        if voted_unsafe is None:
            vote_text = f'5-frame vote: RESET ({true_count}/{win_len})'
            color = (0, 165, 255)
        elif voted_unsafe:
            vote_text = f'5-frame vote: UNSAFE ({true_count}/{win_len}) -> True'
            color = (0, 0, 255)
        else:
            vote_text = f'5-frame vote: SAFE ({true_count}/{win_len}) -> False'
            color = (0, 220, 0)

        # 영상 하단에 투표 결과를 읽기 쉬운 검은 배너로 덧그린다.
        cv2.rectangle(canvas, (0, h - 36), (min(w, 460), h), (20, 20, 20), -1)
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
        # GUI가 없는 환경에서 imshow가 실패하면 노드는 유지하고 프리뷰만 비활성화한다.
        try:
            cv2.imshow(WINDOW_TITLE, canvas)
            cv2.waitKey(1)
        except cv2.error:
            self.show = False

    def _log_callback_error(self, message):
        """로깅 자체의 실패가 타이머 콜백 밖으로 전파되지 않게 한다."""
        try:
            self.get_logger().error(message)
        except Exception:
            pass

    def _log_callback_info(self, message):
        """정보 로그 출력 실패가 콜백 밖으로 전파되지 않게 한다."""
        try:
            self.get_logger().info(message)
        except Exception:
            pass

    def _close_capture_handle(self):
        """OpenCV 프리뷰 창은 유지한 채 VideoCapture 핸들만 안전하게 닫는다."""
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
        """끊긴 웹캠 핸들을 즉시 해제하고 다음 재연결 쿨다운 기준 시각을 기록한다."""
        BedExitNode._close_capture_handle(self)
        self._last_reconnect_attempt = time.monotonic()

    def _open_camera(self, initial: bool = False) -> bool:
        """지정된 camera_index 웹캠을 연다. 실패 시 예외를 던지지 않고 False를 반환한다."""
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
        """웹캠 핸들이 유효한지 확인하고, 끊긴 상태면 쿨다운 주기로 재연결을 시도한다."""
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
        """외부 메서드의 렌더링 실패를 격리하고 프리뷰만 비활성화한다."""
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
        """타이머마다 System Flowchart v5의 Part 5 흐름을 한 번 수행한다."""
        # [PART05-V5: 이미지 캡처] USB 웹캠에서 BGR 프레임 한 장을 읽는다.
        # 이전: BedExitNode.__init__()의 create_timer(..., self.process_frame)
        # 다음: bed_exit_core.BedExitMethod.judge(frame)
        # 프레임 수신 실패 시 거짓 정상(False) 발행을 막기 위해 이력을 초기화하고 발행을 건너뛴다.
        # 유효한 카메라·영상 프레임만 판단 단계로 넘긴다.
        is_video_file = bool(getattr(self, 'is_video_file', False))
        if not is_video_file and not BedExitNode._ensure_camera(self):
            self.state = BedExitState()
            return

        try:
            ok, frame = self.capture.read()
        except Exception as exc:
            BedExitNode._log_callback_error(
                self, f'Video source read failed; no frame decision published: {exc}')
            self.state = BedExitState()
            if not is_video_file and getattr(self, 'camera_index', None) is not None:
                BedExitNode._disconnect_camera(self)
            return

        loop_video = bool(getattr(self, 'loop_video', False))
        if (not ok or frame is None) and is_video_file and loop_video:
            # 반복 경계에서 이전 영상 끝의 투표·추적 상태를 다음 반복에 섞지 않는다.
            self.state = BedExitState()
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
            self.state = BedExitState()
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

        # [PART05-V5-06~08 사람 인식 → 단일 환자 선정 → 환자 마스크 ROI 판정]
        # detector.judge(frame)가 위 세 블록을 수행해 프레임별 True/False를 반환한다.
        # 구현: methods/bed_exit_core.py:BedExitMethod.judge()
        # [PART05-V5-09 5회 프레임 결과 집계 → 3회 이상 이탈 여부]
        # 원시 모델 판정을 즉시 발행하지 않고 공통 5프레임 투표에 입력한다.
        # 구현: bed_exit_state.py:BedExitState.update()
        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            # [PART05-V5-EX 판정 불가 예외 흐름]
            # 사람·마스크 누락이나 계산 실패는 정상(False)이 아니므로 미발행하고 투표만 초기화한다.
            # 판단 객체 내부의 이전 환자 BBox는 유지해 다음 유효 프레임에서 같은 환자를 연결한다.
            self.state = BedExitState()
            BedExitNode._log_callback_error(
                self, f'Judgment failed; history reset: {exc}')
            BedExitNode._render_preview_safely(self, frame, None)
            return
        # [PART05-V5-10 최종 Bool 발행] 유효한 판정과 투표가 모두 끝난 경우에만 발행한다.
        # 출력 계약: /patient/bed_exit, std_msgs/msg/Bool (True=이탈, False=정상)
        try:
            self.publisher.publish(Bool(data=unsafe))
        except Exception as exc:
            self.state = BedExitState()
            BedExitNode._log_callback_error(
                self, f'Publish failed; history reset and no decision published: {exc}')
            BedExitNode._render_preview_safely(self, frame, None)
            return

        # 프리뷰는 발행 이후 실행하며, 화면 표시 여부가 ROS 결과에 영향을 주지 않게 한다.
        BedExitNode._render_preview_safely(self, frame, unsafe)

    def _release_capture(self):
        """웹캠 핸들과 OpenCV 프리뷰 창을 안전하게 해제한다."""
        if self.capture is not None:
            # OpenCV 장치 핸들을 먼저 해제해 다음 실행에서 카메라를 다시 열 수 있게 한다.
            self.capture.release()
            self.capture = None
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

    def _close_detector(self):
        """판단 객체가 선택적으로 제공하는 ``close()``를 호출해 자원을 정리한다."""
        # close()는 선택 규약이므로 메서드가 존재할 때만 호출한다.
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()
        self.detector = None

    def destroy_node(self):
        """카메라와 모델을 먼저 정리한 뒤 ROS 노드 자원을 해제한다."""
        self._release_capture()
        self._close_detector()
        return super().destroy_node()


def main(args=None):
    """ROS를 초기화하고 ``BedExitNode``를 종료 요청까지 실행하는 콘솔 진입점."""
    # [PART05-V5-01 실행 진입점]
    # 이전: setup.py:entry_points의 ``bed_exit = part5_bed_exit.bed_exit_node:main``
    # 다음: BedExitNode.__init__() → BedExitNode.process_frame()
    # ROS 컨텍스트를 초기화한 뒤 노드를 생성하며, 생성 실패에 대비해 초기값은 None으로 둔다.
    rclpy.init(args=args)
    cli_args = rclpy.utilities.remove_ros_args(args=args)[1:]
    cli_video = cli_args[0] if cli_args else None
    node = None
    try:
        # executor가 타이머 콜백 ``process_frame``을 반복 호출하도록 블로킹 실행한다.
        node = BedExitNode(video_path=cli_video)
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # 정상 종료·Ctrl+C·초기화 이후 예외 모두 동일한 정리 경로를 사용한다.
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
