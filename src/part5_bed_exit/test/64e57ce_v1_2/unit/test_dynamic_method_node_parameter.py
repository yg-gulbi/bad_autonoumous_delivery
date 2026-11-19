"""ROS ``method`` 파라미터가 동적 로더까지 전달되는지 검증한다."""

from types import SimpleNamespace

import rclpy

from part5_bed_exit import bed_exit_node


class _FakeCapture:
    def isOpened(self):
        return True

    def release(self):
        pass


def test_method_parameter_reaches_dynamic_loader(monkeypatch):
    calls = []
    detector = SimpleNamespace(judge=lambda frame: False)

    monkeypatch.setattr(
        bed_exit_node,
        'load_bed_exit_method',
        lambda method, config: calls.append((method, config)) or detector,
    )
    monkeypatch.setattr(bed_exit_node.cv2, 'VideoCapture', lambda index: _FakeCapture())

    rclpy.init(
        args=[
            '--ros-args',
            '-p',
            'method:=test_package.detector:CustomDetector',
            '-p',
            'show:=false',
        ]
    )
    node = None
    try:
        node = bed_exit_node.BedExitNode()
        assert calls == [('test_package.detector:CustomDetector', '')]
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
