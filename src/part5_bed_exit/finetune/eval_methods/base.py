"""파인튜닝 모델 비교용 외부 메서드들의 공통 설정·회전·환경변수 오버라이드 유틸리티.

지원 환경변수:
- ``PART5_EVAL_CONFIG``: ROI/model_dir/device/weights JSON 경로
  (미지정 시 ``config/cam_roi.json`` → ``config/methods.local.json`` 순 자동 탐색)
- ``PART5_EVAL_WEIGHTS``: 가중치 파일명(``model_dir`` 기준) 또는 절대경로 (예: ``best.pt``)
- ``PART5_EVAL_DEVICE``: 추론 장치 덮어쓰기 (예: ``cuda:0``, ``cpu``)
- ``PART5_EVAL_CONF``: YOLO 사람 검출 최소 신뢰도 덮어쓰기 (기본 ``0.5``)
- ``PART5_EVAL_OUTSIDE``: ROI 밖 면적 이탈 기준 덮어쓰기 (기본 ``0.05``)
- ``PART5_EVAL_MIN_IOU``: 단일 환자 추적 최소 IoU 덮어쓰기 (기본 ``0.15``)
"""

import os
from pathlib import Path

import cv2
import numpy as np

from part5_bed_exit.methods.bed_exit_core import (
    PatientSelector,
    PolygonROI,
    YOLO_CONFIDENCE,
)
from part5_bed_exit.pretrained import read_config, resolve_weights_path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_CANDIDATES = (
    PACKAGE_ROOT / 'config' / 'cam_roi.json',
    PACKAGE_ROOT / 'config' / 'methods.local.json',
)


def rotate_vertices_ccw90(vertices) -> list[list[float]]:
    """정규화 좌표 ``[x, y]``를 왼쪽(반시계) 90도 회전 좌표 ``[y, 1 - x]``로 변환한다."""
    return [[float(y), float(1.0 - x)] for x, y in vertices]


def rotate_frame_ccw90(frame):
    """BGR 프레임을 왼쪽(반시계) 90도 회전한다 (머리 우·발 좌 → 머리 위·발 아래)."""
    if (not isinstance(frame, np.ndarray) or frame.ndim != 3
            or frame.shape[2] != 3 or frame.size == 0):
        return frame
    return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    return float(raw)


def resolve_eval_config_path() -> Path:
    """비교 평가 메서드가 읽을 JSON 설정 파일 경로를 결정한다."""
    env_path = os.environ.get('PART5_EVAL_CONFIG', '').strip()
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'PART5_EVAL_CONFIG not found: {path}')
        return path

    for candidate in DEFAULT_CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()

    tried = ', '.join(str(p) for p in DEFAULT_CONFIG_CANDIDATES)
    raise FileNotFoundError(
        f'Set PART5_EVAL_CONFIG or create one of: {tried}'
    )


def load_eval_runtime(method_name: str, *, rotate_ccw90: bool = False) -> dict:
    """JSON과 환경변수 오버라이드를 합쳐 모델 생성 인자를 반환한다."""
    config_path = resolve_eval_config_path()
    config, roi, model_dir = read_config(config_path)

    if rotate_ccw90:
        roi = PolygonROI(rotate_vertices_ccw90(roi.vertices))

    env_weights = os.environ.get('PART5_EVAL_WEIGHTS', '').strip()
    if env_weights:
        candidate = Path(env_weights).expanduser()
        weights = candidate if candidate.is_absolute() else (model_dir / candidate)
    else:
        weights = resolve_weights_path(config, method_name, model_dir)

    device = os.environ.get('PART5_EVAL_DEVICE', '').strip() or config.get('device', 'cpu')
    confidence = _float_env(
        'PART5_EVAL_CONF',
        float(config.get('confidence', YOLO_CONFIDENCE)),
    )
    outside_fraction = _float_env(
        'PART5_EVAL_OUTSIDE',
        float(config.get('outside_fraction', 0.05)),
    )
    min_iou = _float_env(
        'PART5_EVAL_MIN_IOU',
        float(config.get('min_iou', 0.15)),
    )
    return {
        'config_path': config_path,
        'roi': roi,
        'weights': weights,
        'device': device,
        'confidence': confidence,
        'outside_fraction': outside_fraction,
        'min_iou': min_iou,
    }


def apply_selector_min_iou(method_obj, roi: PolygonROI, min_iou: float):
    """판단기의 환자 추적 ``min_iou``를 설정값으로 교체한다."""
    method_obj.selector = PatientSelector(roi, min_iou=min_iou)
