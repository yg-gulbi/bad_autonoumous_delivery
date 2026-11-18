"""ROS-independent obstacle classification and depth-to-3D pipeline."""

from dataclasses import dataclass
from importlib import import_module
import math

import numpy as np


@dataclass(frozen=True)
class Detection:
    """One object: label, confidence, pixel (x1, y1, x2, y2), and optional map pose."""

    label: str
    confidence: float
    bbox: tuple[float, float, float, float]
    pose: dict[str, float] | None = None

    def to_dict(self) -> dict:
        data = {
            'label': self.label,
            'confidence': self.confidence,
            'bbox': self.bbox,
        }
        if self.pose is not None:
            data['pose'] = self.pose
        return data


class UnconfiguredClassifier:
    """Fail explicitly until a real model adapter is selected."""

    def classify(self, frame):
        raise RuntimeError('Classifier is not configured; set method:=module:ClassName')


class Yolo11Classifier:
    """Ultralytics YOLO11n adapter implementing classify(frame) -> list[Detection]."""

    def __init__(self, model_path: str = 'yolo11n.pt', conf_threshold: float = 0.25):
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.conf_threshold = float(conf_threshold)

    def classify(self, frame: np.ndarray) -> list[Detection]:
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
            x1 = max(0.0, min(x1, float(width)))
            y1 = max(0.0, min(y1, float(height)))
            x2 = max(0.0, min(x2, float(width)))
            y2 = max(0.0, min(y2, float(height)))
            if x2 > x1 and y2 > y1:
                detections.append(Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2)))
        return detections


def load_classifier(path: str, **kwargs):
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
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0):
        raise ValueError('Expected a non-empty uint8 BGR image with three channels')


def preprocess(frame):
    """Preserve source coordinates; model-specific resizing belongs in the adapter."""
    validate_frame(frame)
    return frame


def classify_obstacles(frame, classifier):
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
    """Return depth in meters if finite and within [min_depth, max_depth], else None."""
    if depth_image is None or depth_image.size == 0:
        return None
    height, width = depth_image.shape[:2]
    if not (0 <= u < width and 0 <= v < height):
        return None

    raw = float(depth_image[v, u])
    z = raw / 1000.0 if np.issubdtype(depth_image.dtype, np.integer) else raw
    if not math.isfinite(z) or not (min_depth < z < max_depth):
        return None
    return z


def deproject_pixel_to_3d(u: int, v: int, z: float, K: np.ndarray) -> tuple[float, float, float]:
    """Back-project pixel (u, v) and depth z (m) to camera 3D coordinates (X, Y, Z)."""
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
    return {
        'status': 'ok',
        'header': header,
        'obstacle_present': bool(detections),
        'detections': [item.to_dict() for item in detections],
    }


def build_error(status: str, message: str, header=None) -> dict:
    """Unknown obstacle presence must never be represented as clear."""
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
