"""단일 설정 JSON으로 운영·연구용 판단 객체를 생성하는 파일.

운영 노드는 설정의 ``method``를 사용한다. 연구 비교는 같은 함수에 비교할 메서드 이름을
명시해 설정값을 덮어쓴다. 5프레임 투표는 여기서 수행하지 않는다.

System Flowchart v5의 실행 전 준비는 ``create_method()``에 대응한다.
"""

import json
from pathlib import Path

from part5_bed_exit.methods.bed_exit_core import (
    MODEL_FILES,
    YOLO_CONFIDENCE,
    DetectionROI,
    MediaPipePoseROI,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
)


SUPPORTED_METHODS = tuple(MODEL_FILES)


def read_config(path):
    """설정 JSON을 검증하고 ROI와 JSON 기준 절대 모델 경로를 반환한다."""
    # [PART05-V5-03A 설정 읽기]
    # 호출자: 운영·연구 객체 생성 함수; 다음: PolygonROI와 각 모델 클래스 생성자
    # 사용자 홈 표기와 상대경로를 제거해 이후 모든 파일 해석의 기준점을 고정한다.
    path = Path(path).expanduser().resolve()
    config = json.loads(path.read_text(encoding='utf-8'))

    # 정규화 꼭짓점의 범위·면적·자기 교차 검증은 PolygonROI가 담당한다.
    roi = PolygonROI(config['roi'])

    # 모델 상대경로는 실행 셸이 아니라 설정 JSON이 놓인 디렉터리를 기준으로 해석한다.
    model_dir = Path(config['model_dir']).expanduser()
    if not model_dir.is_absolute():
        model_dir = path.parent / model_dir
    return config, roi, model_dir.resolve()


def create_method(config_path, method_name=None):
    """설정에 선택된 판단 모델 객체 하나를 생성한다.

    운영에서는 ``method_name``을 생략해 JSON의 ``method``를 사용한다. 여러 모델을 같은
    조건에서 비교하는 연구 코드만 ``method_name``을 전달해 선택값을 덮어쓴다.
    """
    if not config_path:
        raise ValueError('Set method_config')

    config, roi, model_dir = read_config(config_path)
    name = method_name if method_name is not None else config.get('method')
    if name not in SUPPORTED_METHODS:
        raise ValueError(f'Unknown method {name!r}; choose from {SUPPORTED_METHODS}')

    weights = model_dir / MODEL_FILES[name]
    device = config.get('device', 'cpu')

    if name == 'detection':
        return DetectionROI(
            roi, weights, device=device, confidence=YOLO_CONFIDENCE,
            outside_fraction=0.05,
        )
    if name == 'segmentation':
        return SegmentationROI(
            roi, weights, device=device, confidence=YOLO_CONFIDENCE,
            outside_fraction=0.05,
        )
    if name == 'yolo_pose':
        return YoloPoseROI(roi, weights, device=device, confidence=YOLO_CONFIDENCE)
    if name == 'mediapipe_pose':
        return MediaPipePoseROI(roi, weights)

    # 위의 지원 목록 검사 때문에 도달하지 않는다.
    raise ValueError(f'Unknown method {name!r}')
