"""ROS 2에 의존하지 않는 순수 장애물 분류 및 Depth 기반 3D 위치 변환 파이프라인."""

from dataclasses import dataclass
from importlib import import_module
import math

import numpy as np


@dataclass(frozen=True)
class Detection:
    """탐지된 단일 객체 정보 (클래스명, 신뢰도, 픽셀 좌표 bbox, 선택적 map 좌표 pose)."""

    label: str
    confidence: float
    bbox: tuple[float, float, float, float]
    pose: dict[str, float] | None = None

    def to_dict(self) -> dict:
        """JSON 직렬화를 위해 딕셔너리로 변환. 유효한 Depth가 없어 pose가 None이면 pose 키를 생략한다."""
        data = {
            'label': self.label,
            'confidence': self.confidence,
            'bbox': self.bbox,
        }
        if self.pose is not None:
            data['pose'] = self.pose
        return data


class UnconfiguredClassifier:
    """모델 어댑터(method)가 지정되지 않았을 때 명시적으로 예외를 발생시키는 기본 클래스."""

    def classify(self, frame):
        raise RuntimeError('Classifier is not configured; set method:=module:ClassName')


class Yolo11Classifier:
    """Ultralytics YOLO11n 모델로 BGR 이미지에서 객체를 탐지하는 어댑터 클래스."""

    def __init__(self, model_path: str = 'yolo11n.pt', conf_threshold: float = 0.25):
        # YOLO 패키지가 설치되지 않은 환경에서도 모듈 임포트·단위 테스트가 가능하도록 지연 임포트
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.conf_threshold = float(conf_threshold)

    def classify(self, frame: np.ndarray) -> list[Detection]:
        """BGR 이미지를 입력받아 이미지 경계 안으로 보정된 Detection 목록을 반환."""
        height, width = frame.shape[:2]
        results = self.model(frame, conf=self.conf_threshold, verbose=False)
        detections: list[Detection] = []
        if not results:
            return detections

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            label = str(self.model.names[cls_id])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
            # 모델 출력 좌표가 소수 오차로 이미지 경계를 살짝 벗어나지 않도록 클리핑
            x1 = max(0.0, min(x1, float(width)))
            y1 = max(0.0, min(y1, float(height)))
            x2 = max(0.0, min(x2, float(width)))
            y2 = max(0.0, min(y2, float(height)))
            if x2 > x1 and y2 > y1:
                detections.append(Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2)))
        return detections


def load_classifier(path: str, **kwargs):
    """'패키지.모듈:클래스명' 문자열을 받아 분류기 인스턴스를 동적으로 생성해 반환."""
    if not path:
        return UnconfiguredClassifier()
    module, separator, name = path.partition(':')
    if not separator or not module or not name:
        raise ValueError('method must be module:ClassName')
    cls = getattr(import_module(module), name)
    classifier = cls(**kwargs) if kwargs else cls()
    if not callable(getattr(classifier, 'classify', None)):
        raise TypeError('Classifier must implement classify(frame) -> list[Detection]')
    return classifier


def validate_frame(frame):
    """입력이 비어 있지 않은 3채널 uint8 BGR 이미지인지 검증."""
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0):
        raise ValueError('Expected a non-empty uint8 BGR image with three channels')


def preprocess(frame):
    """원본 픽셀 좌표를 유지하기 위해 형태 검증만 수행 (모델별 리사이즈는 어댑터 내부에서 처리)."""
    validate_frame(frame)
    return frame


def classify_obstacles(frame, classifier):
    """분류기를 실행하고 반환된 Detection(라벨, 신뢰도, bbox 범위)의 유효성을 검증."""
    detections = classifier.classify(frame)
    if not isinstance(detections, list):
        raise TypeError('classify() must return list[Detection]')
    height, width = frame.shape[:2]
    for item in detections:
        if not isinstance(item, Detection):
            raise TypeError('Every result must be a Detection')
        if (not isinstance(item.label, str) or not item.label.strip()
                or not math.isfinite(item.confidence) or not 0 <= item.confidence <= 1):
            raise ValueError('Invalid label or confidence')
        if len(item.bbox) != 4 or not all(math.isfinite(v) for v in item.bbox):
            raise ValueError('Invalid bounding box')
        x1, y1, x2, y2 = item.bbox
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError('Bounding box must be inside the source image')
    return detections


def extract_valid_depth(
    depth_image: np.ndarray,
    u: int,
    v: int,
    min_depth: float = 0.2,
    max_depth: float = 5.0,
) -> float | None:
    """픽셀 (u, v)의 깊이(m)가 유한값이고 유효 범위(min_depth < z < max_depth)일 때만 반환."""
    if depth_image is None or depth_image.size == 0:
        return None
    height, width = depth_image.shape[:2]
    if not (0 <= u < width and 0 <= v < height):
        return None

    raw = float(depth_image[v, u])
    # 정수형(uint16, mm 단위)은 m 단위로 변환, 실수형(float32)은 그대로 사용
    z = raw / 1000.0 if np.issubdtype(depth_image.dtype, np.integer) else raw
    if not math.isfinite(z) or not (min_depth < z < max_depth):
        return None
    return z


def deproject_pixel_to_3d(u: int, v: int, z: float, K: np.ndarray) -> tuple[float, float, float]:
    """카메라 내부 파라미터 K를 이용해 픽셀 (u, v)와 깊이 z(m)를 카메라 3D 좌표 (X, Y, Z)로 역투영."""
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    return ((u - cx) * z / fx, (v - cy) * z / fy, z)


def attach_poses(
    detections: list[Detection],
    depth_image: np.ndarray | None,
    K: np.ndarray | None,
    transform_fn=None,
    min_depth: float = 0.2,
    max_depth: float = 5.0,
) -> list[Detection]:
    """Attach 3D/map pose only to detections with valid depth; keep others pose-free."""
    if depth_image is None or K is None:
        return detections

    enriched: list[Detection] = []
    for item in detections:
        x1, y1, x2, y2 = item.bbox
        # 바운딩 박스 중심 픽셀에서 Depth 추출
        u = int((x1 + x2) // 2)
        v = int((y1 + y2) // 2)
        z = extract_valid_depth(depth_image, u, v, min_depth=min_depth, max_depth=max_depth)
        if z is None:
            # Depth가 무효(0/NaN/범위 밖)이면 TF 조회 없이 감지 정보만 유지
            enriched.append(Detection(item.label, item.confidence, item.bbox, pose=None))
            continue

        # Depth가 유효할 때만 3D 역투영 후 map 좌표계로 변환
        x, y, z_cam = deproject_pixel_to_3d(u, v, z, K)
        pose = transform_fn(x, y, z_cam) if transform_fn is not None else {'x': x, 'y': y, 'z': z_cam}
        enriched.append(Detection(item.label, item.confidence, item.bbox, pose=pose))
    return enriched


def build_result(detections: list[Detection], header) -> dict:
    """정상 처리 결과 딕셔너리 생성 (탐지 객체 유무에 따라 obstacle_present가 True/False)."""
    return {
        'status': 'ok',
        'header': header,
        'obstacle_present': bool(detections),
        'detections': [item.to_dict() for item in detections],
    }


def build_error(status: str, message: str, header=None) -> dict:
    """오류/타임아웃 응답 생성. 장애물 없음(False)과 혼동되지 않도록 obstacle_present는 None으로 둔다."""
    return {
        'status': status,
        'header': header,
        'obstacle_present': None,
        'detections': [],
        'error': message,
    }


def process_frame(
    frame,
    classifier,
    header,
    depth_image=None,
    K=None,
    transform_fn=None,
    min_depth: float = 0.2,
    max_depth: float = 5.0,
) -> dict:
    """전체 처리 파이프라인: 프레임 검증 -> 객체 분류 -> 유효 Depth 기반 3D/map 위치 계산 -> 결과 생성."""
    detections = classify_obstacles(preprocess(frame), classifier)
    detections = attach_poses(
        detections,
        depth_image=depth_image,
        K=K,
        transform_fn=transform_fn,
        min_depth=min_depth,
        max_depth=max_depth,
    )
    return build_result(detections, header)


def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    """탐지된 객체의 바운딩 박스, 라벨, 신뢰도 및 map 좌표를 BGR 이미지에 그려 반환."""
    import cv2

    annotated = frame.copy()
    for item in detections:
        x1, y1, x2, y2 = map(int, item['bbox'])
        label = item['label']
        conf = item['confidence']
        pose = item.get('pose')
        text = f'{label} {conf:.2f}'
        if pose is not None:
            # 유효한 Depth로 map 좌표가 계산된 경우 함께 표기
            text += f" ({pose['x']:.2f}, {pose['y']:.2f})"
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            text,
            (x1, max(15, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
        )
    return annotated
