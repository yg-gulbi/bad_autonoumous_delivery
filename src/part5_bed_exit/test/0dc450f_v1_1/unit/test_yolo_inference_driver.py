"""단위 테스트: 가짜 YOLO Driver로 모델 호출 입출력 계약을 검증한다.

실제 가중치 대신 ``YoloDriverStub``을 주입한다. 한 프레임을 ``judge()``에 넣었을 때
사람 클래스만 confidence 0.5로 요청하고, 가짜 BBox 결과가 최종 bool로 이어지는지를
함수·객체·모듈 경계에서 한 번에 확인한다.
"""

from types import SimpleNamespace

import numpy as np

from part5_bed_exit.methods.bed_exit_core import DetectionROI, PolygonROI


class TensorStub:
    """Ultralytics Tensor의 테스트에 필요한 ``cpu().numpy()``만 제공한다."""

    def __init__(self, value):
        self.value = np.asarray(value)

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class YoloDriverStub:
    """YOLO ``predict`` 입력을 기록하고 정해진 사람 BBox를 반환하는 Driver Stub."""

    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        boxes = SimpleNamespace(
            xyxy=TensorStub([[20.0, 20.0, 80.0, 80.0]]),
            conf=TensorStub([0.9]),
        )
        return [SimpleNamespace(boxes=boxes, masks=None, keypoints=None)]


def test_yolo_driver_uses_confidence_050_and_returns_python_bool():
    """[UT-YOLO-001] confidence 0.5 호출과 BBox 판정 bool 출력을 검증한다."""
    model = YoloDriverStub()
    roi = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    method = DetectionROI(roi, 'unused.pt', model=model)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    decision = method.judge(frame)

    assert type(decision) is bool
    assert decision is False
    assert len(model.calls) == 1
    assert model.calls[0]['classes'] == [0]
    assert model.calls[0]['conf'] == 0.5
    assert model.calls[0]['imgsz'] == 640
    assert model.calls[0]['retina_masks'] is True
