"""단위 테스트: 가짜 카메라 Driver로 운영 노드의 30Hz 설정을 검증한다.

이 테스트는 실제 웹캠 처리량을 측정하지 않는다. 기본 ``capture_fps=30.0``이 ROS
타이머 주기 ``1/30초``로 연결되는지만 확인한다. 실제 카메라 FPS와 모델 처리 FPS는
장치·가중치가 필요한 통합 성능 실험에서 측정해야 한다.
"""

import rclpy

from part5_bed_exit import bed_exit_node


class CaptureDriverStub:
    """장치를 열지 않고 열린 카메라처럼 동작하는 OpenCV Driver Stub."""

    def isOpened(self):
        return True

    def release(self):
        pass


class MethodStub:
    """노드 생성에 필요한 최소 판단 객체 계약만 제공한다."""

    def judge(self, frame):
        return False


def test_default_capture_fps_creates_30hz_timer(monkeypatch):
    """[UT-NODE-001] 기본값 30FPS가 정확히 1/30초 타이머가 되는지 검증한다."""
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: CaptureDriverStub())
    monkeypatch.setenv('ROS_LOG_DIR', '/tmp')
    owns_context = not rclpy.ok()
    if owns_context:
        # 단위 테스트 로그는 사용자 홈이 아니라 임시 디렉터리에 기록한다.
        rclpy.init()
    node = None
    try:
        node = bed_exit_node.BedExitNode(detector=MethodStub())
        assert node.get_parameter('capture_fps').value == 30.0
        assert abs(node.timer.timer_period_ns - round(1_000_000_000 / 30.0)) <= 1
    finally:
        if node is not None:
            node.destroy_node()
        if owns_context and rclpy.ok():
            rclpy.shutdown()
