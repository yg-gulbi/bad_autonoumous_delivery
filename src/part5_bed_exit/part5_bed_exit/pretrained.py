"""단일 설정 JSON과 선택적 파라미터 오버라이드로 운영·연구용 판단 객체를 생성하는 파일.

운영 노드는 설정의 ``method``를 사용한다. 연구 비교는 같은 함수에 비교할 메서드 이름을
명시해 설정값을 덮어쓴다. 다수결 투표는 여기서 수행하지 않는다.

선택적 ``weights`` 키나 모델 파라미터(``yolo_confidence``, ``yolo_imgsz``,
``outside_fraction``, ``mask_threshold``, ``selector_*``, ``pose_*``, ``mediapipe_*``,
``frame_rotate_ccw`` 등)는 JSON에 직접 넣거나 ROS 2 파라미터 ``overrides``로 덮어쓸 수 있다.
또한 선택적으로 ``method: "ensemble"`` 또는 ``ensemble`` 설정으로 여러 모델을 결합할 수 있다.
우선순위는 ``overrides > JSON 설정 > v1.3 기본값``이다.

System Flowchart v5의 실행 전 준비는 ``create_method()``에 대응한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from part5_bed_exit.bed_exit_method import BedExitMethod as FrameMethod
from part5_bed_exit.bed_exit_method import validate_bed_exit_method
from part5_bed_exit.methods.bed_exit_core import (
    MODEL_FILES,
    YOLO_CONFIDENCE,
    DetectionROI,
    JudgmentUnavailable,
    MediaPipePoseROI,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
)


SUPPORTED_METHODS = tuple(MODEL_FILES)
BUILTIN_METHODS = SUPPORTED_METHODS + ('ensemble',)
ENSEMBLE_POLICIES = ('majority', 'any', 'all')
VALID_ROTATION_ANGLES = (0, 90, 180, 270)
SUPPORTED_MODEL_OVERRIDES = frozenset({
    'weights',
    'device',
    'yolo_confidence',
    'yolo_imgsz',
    'outside_fraction',
    'mask_threshold',
    'selector_min_iou',
    'selector_init_overlap',
    'selector_ambiguity_gap',
    'pose_policy',
    'pose_point_conf',
    'pose_tolerance_px',
    'mediapipe_num_poses',
    'mediapipe_min_detection_conf',
    'mediapipe_min_presence_conf',
    'frame_rotate_ccw',
    'roi_already_rotated',
    'ensemble_methods',
    'ensemble_policy',
    'ensemble_min_votes',
    'ensemble_require_all_valid',
})
_ENSEMBLE_ONLY_KEYS = frozenset({
    'ensemble_methods',
    'ensemble_policy',
    'ensemble_min_votes',
    'ensemble_require_all_valid',
})


class EnsembleBedExitMethod(FrameMethod):
    """여러 판단 모델의 프레임 판정을 정책(majority/any/all/min_votes)으로 결합한다."""

    def __init__(
        self,
        members,
        *,
        policy: str = 'majority',
        min_votes: int = 0,
        require_all_valid: bool = False,
    ):
        validated = [validate_bed_exit_method(member) for member in members]
        if len(validated) < 2:
            raise ValueError('Ensemble requires at least 2 member methods')
        if policy not in ENSEMBLE_POLICIES:
            raise ValueError(
                f'ensemble policy must be one of {ENSEMBLE_POLICIES}; got {policy!r}'
            )
        if type(min_votes) is not int or min_votes < 0 or min_votes > len(validated):
            raise ValueError(f'ensemble min_votes must be an int in [0, {len(validated)}]')
        if type(require_all_valid) is not bool:
            raise TypeError('ensemble require_all_valid must be a Python bool')

        self.members = tuple(validated)
        self.policy = policy
        self.min_votes = min_votes
        self.require_all_valid = require_all_valid
        self.last_votes: list[bool] = []

    def judge(self, frame) -> bool:
        votes: list[bool] = []
        for member in self.members:
            try:
                result = member.judge(frame)
            except JudgmentUnavailable:
                if self.require_all_valid:
                    raise
                continue
            if type(result) is not bool:
                raise TypeError('Ensemble member must return a Python bool')
            votes.append(result)

        self.last_votes = votes
        if not votes:
            raise JudgmentUnavailable('all_ensemble_members_unavailable')

        true_count = sum(votes)
        if self.min_votes > 0:
            return true_count >= self.min_votes
        if self.policy == 'any':
            return true_count > 0
        if self.policy == 'all':
            return true_count == len(votes)
        return true_count > (len(votes) / 2.0)

    def reset(self) -> None:
        self.last_votes = []
        for member in self.members:
            reset = getattr(member, 'reset', None)
            if callable(reset):
                reset()

    def render(self, frame):
        for member in self.members:
            render = getattr(member, 'render', None)
            if callable(render):
                return render(frame)
        return frame.copy()

    def close(self) -> None:
        for member in self.members:
            close = getattr(member, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def validate_rotation_angle(angle_ccw: int) -> int:
    """지원되는 반시계 회전 각도(0, 90, 180, 270)인지 검증한다."""
    if type(angle_ccw) is not int or angle_ccw not in VALID_ROTATION_ANGLES:
        raise ValueError(
            f'frame_rotate_ccw must be one of {VALID_ROTATION_ANGLES}; got {angle_ccw!r}'
        )
    return angle_ccw


def rotate_frame_ccw(frame, angle_ccw: int):
    """OpenCV BGR 프레임을 반시계 방향으로 ``angle_ccw``도 회전한다."""
    angle = validate_rotation_angle(angle_ccw)
    if angle == 0:
        return frame
    if angle == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if angle == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)


def rotate_roi_ccw(roi, angle_ccw: int):
    """정규화 다각형 ROI를 프레임의 반시계 회전(``angle_ccw``)과 같은 좌표계로 변환한다."""
    angle = validate_rotation_angle(angle_ccw)
    if angle == 0:
        return roi

    vertices = roi.vertices
    if angle == 90:
        rotated = [[float(y), float(1.0 - x)] for x, y in vertices]
    elif angle == 180:
        rotated = [[float(1.0 - x), float(1.0 - y)] for x, y in vertices]
    else:  # 270 CCW == 90 CW
        rotated = [[float(1.0 - y), float(x)] for x, y in vertices]
    return PolygonROI(rotated)


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


def resolve_weights_path(config, method_name: str, model_dir: Path,
                         explicit_method: str | None = None) -> Path:
    """설정 JSON의 선택적 ``weights`` 키 또는 기본 ``MODEL_FILES``로 가중치 경로를 해석한다.

    - ``weights`` 키가 없거나 비어 있으면 기존 기본 파일명(``MODEL_FILES[method_name]``) 사용
    - ``weights``가 문자열이면 설정의 기본 ``method``와 일치할 때 해당 파일명/절대경로 사용
      (4모델 비교처럼 ``explicit_method``로 다른 내장 메서드를 지정한 경우 해당 기본 파일명 유지)
    - ``weights``가 ``{method: filename}`` 딕셔너리면 ``method_name``에 대응하는 값 사용
    """
    weights_cfg = config.get('weights')
    selected = None
    if weights_cfg is None or weights_cfg == '':
        selected = None
    elif isinstance(weights_cfg, str):
        config_method = config.get('method')
        if explicit_method is None or config_method is None or explicit_method == config_method:
            selected = weights_cfg.strip() or None
    elif isinstance(weights_cfg, dict):
        raw = weights_cfg.get(method_name)
        if raw is not None and not isinstance(raw, str):
            raise ValueError(f'weights[{method_name!r}] must be a filename or path string')
        selected = raw.strip() if isinstance(raw, str) and raw.strip() else None
    else:
        raise ValueError('weights must be a filename/path string or a {method: filename} dict')

    if not selected:
        return model_dir / MODEL_FILES[method_name]

    candidate = Path(selected).expanduser()
    return candidate if candidate.is_absolute() else (model_dir / candidate)


def _pick_option(overrides: dict | None, config: dict, *keys: str):
    """``overrides > config`` 순서로 지정된 키 중 첫 번째 비-None 값을 반환한다."""
    if overrides:
        for key in keys:
            if key in overrides and overrides[key] is not None:
                return True, overrides[key]
    for key in keys:
        if key in config and config[key] is not None:
            return True, config[key]
    return False, None


def _build_single_method(
    name: str,
    config: dict,
    roi,
    model_dir: Path,
    explicit_method: str | None,
    overrides: dict | None,
):
    """내장 단일 모델 4종 중 하나를 생성한다."""
    if name not in SUPPORTED_METHODS:
        raise ValueError(f'Unknown method {name!r}; choose from {SUPPORTED_METHODS}')

    override_weights = overrides.get('weights') if overrides else None
    if override_weights is not None and override_weights != '':
        if not isinstance(override_weights, str):
            raise ValueError('weights override must be a filename or path string')
        selected_weights = override_weights.strip()
        if selected_weights:
            candidate = Path(selected_weights).expanduser()
            weights = candidate if candidate.is_absolute() else (model_dir / candidate)
        else:
            weights = resolve_weights_path(config, name, model_dir, explicit_method=explicit_method)
    else:
        weights = resolve_weights_path(config, name, model_dir, explicit_method=explicit_method)

    has_device, device_val = _pick_option(overrides, config, 'device')
    device = str(device_val).strip() if has_device and str(device_val).strip() else 'cpu'

    has_conf, conf_val = _pick_option(overrides, config, 'yolo_confidence', 'confidence')
    confidence = float(conf_val) if has_conf else YOLO_CONFIDENCE

    # 선택적 고급 파라미터는 JSON 또는 overrides에 명시된 경우에만 생성자로 전달한다.
    # (미지정 시 기존 v1.3 생성자 기본값과 기존 단위 테스트의 kwargs 계약을 그대로 유지)
    optional_selector_kwargs: dict[str, float] = {}
    for kwarg_name, option_keys in (
        ('min_iou', ('selector_min_iou', 'min_iou')),
        ('init_overlap', ('selector_init_overlap', 'init_overlap')),
        ('ambiguity_gap', ('selector_ambiguity_gap', 'ambiguity_gap')),
    ):
        found, value = _pick_option(overrides, config, *option_keys)
        if found:
            optional_selector_kwargs[kwarg_name] = float(value)

    optional_yolo_kwargs: dict = dict(optional_selector_kwargs)
    has_imgsz, imgsz_val = _pick_option(overrides, config, 'yolo_imgsz', 'imgsz')
    if has_imgsz:
        optional_yolo_kwargs['imgsz'] = imgsz_val

    if name == 'detection':
        has_out, out_val = _pick_option(overrides, config, 'outside_fraction')
        outside_fraction = float(out_val) if has_out else 0.05
        return DetectionROI(
            roi,
            weights,
            device=device,
            confidence=confidence,
            outside_fraction=outside_fraction,
            **optional_yolo_kwargs,
        )

    if name == 'segmentation':
        has_out, out_val = _pick_option(overrides, config, 'outside_fraction')
        outside_fraction = float(out_val) if has_out else 0.05
        seg_kwargs = dict(optional_yolo_kwargs)
        has_mask, mask_val = _pick_option(overrides, config, 'mask_threshold')
        if has_mask:
            seg_kwargs['mask_threshold'] = float(mask_val)
        return SegmentationROI(
            roi,
            weights,
            device=device,
            confidence=confidence,
            outside_fraction=outside_fraction,
            **seg_kwargs,
        )

    if name == 'yolo_pose':
        pose_kwargs = dict(optional_yolo_kwargs)
        has_policy, policy_val = _pick_option(overrides, config, 'pose_policy', 'policy')
        if has_policy and str(policy_val).strip():
            pose_kwargs['policy'] = str(policy_val).strip()
        has_pconf, pconf_val = _pick_option(overrides, config, 'pose_point_conf', 'point_conf')
        if has_pconf:
            pose_kwargs['point_conf'] = float(pconf_val)
        has_tol, tol_val = _pick_option(overrides, config, 'pose_tolerance_px', 'tolerance_px')
        if has_tol:
            pose_kwargs['tolerance_px'] = float(tol_val)
        return YoloPoseROI(
            roi,
            weights,
            device=device,
            confidence=confidence,
            **pose_kwargs,
        )

    if name == 'mediapipe_pose':
        mp_kwargs: dict = dict(optional_selector_kwargs)
        has_policy, policy_val = _pick_option(overrides, config, 'pose_policy', 'policy')
        if has_policy and str(policy_val).strip():
            mp_kwargs['policy'] = str(policy_val).strip()
        has_pconf, pconf_val = _pick_option(overrides, config, 'pose_point_conf', 'point_conf')
        if has_pconf:
            mp_kwargs['point_conf'] = float(pconf_val)
        has_tol, tol_val = _pick_option(overrides, config, 'pose_tolerance_px', 'tolerance_px')
        if has_tol:
            mp_kwargs['tolerance_px'] = float(tol_val)
        has_num, num_val = _pick_option(overrides, config, 'mediapipe_num_poses', 'num_poses')
        if has_num:
            mp_kwargs['num_poses'] = num_val
        has_det, det_val = _pick_option(
            overrides, config, 'mediapipe_min_detection_conf', 'min_pose_detection_confidence'
        )
        if has_det:
            mp_kwargs['min_pose_detection_confidence'] = float(det_val)
        has_pres, pres_val = _pick_option(
            overrides, config, 'mediapipe_min_presence_conf', 'min_pose_presence_confidence'
        )
        if has_pres:
            mp_kwargs['min_pose_presence_confidence'] = float(pres_val)
        return MediaPipePoseROI(roi, weights, **mp_kwargs)

    raise ValueError(f'Unknown method {name!r}')


def _parse_ensemble_specs(raw_specs) -> list[dict]:
    """문자열(쉼표 구분) 또는 리스트에서 앙상블 멤버 명세 목록을 파싱한다."""
    if isinstance(raw_specs, str):
        items = [part.strip() for part in raw_specs.split(',') if part.strip()]
    elif isinstance(raw_specs, (list, tuple)):
        items = list(raw_specs)
    else:
        raise ValueError('ensemble models must be a list or comma-separated string')

    specs: list[dict] = []
    for item in items:
        if isinstance(item, str):
            specs.append({'method': item.strip()})
        elif isinstance(item, dict):
            if not item.get('method'):
                raise ValueError('Each ensemble model entry must specify "method"')
            specs.append(dict(item))
        else:
            raise ValueError('Each ensemble model entry must be a method name string or dict')
    return specs


def _build_ensemble_method(
    config: dict,
    roi,
    model_dir: Path,
    overrides: dict | None,
) -> EnsembleBedExitMethod:
    """JSON ``ensemble`` 블록 또는 ``overrides['ensemble_methods']``로 앙상블 객체를 생성한다."""
    ensemble_cfg = config.get('ensemble')
    if ensemble_cfg is not None and not isinstance(ensemble_cfg, dict):
        raise ValueError('config["ensemble"] must be an object')
    ensemble_cfg = ensemble_cfg or {}

    raw_models = None
    if overrides and overrides.get('ensemble_methods'):
        raw_models = overrides['ensemble_methods']
    elif 'models' in ensemble_cfg:
        raw_models = ensemble_cfg['models']
    elif 'ensemble_methods' in config and config['ensemble_methods']:
        raw_models = config['ensemble_methods']
    if not raw_models:
        raise ValueError('Ensemble requires ensemble.models or ensemble_methods with >= 2 methods')

    specs = _parse_ensemble_specs(raw_models)
    if len(specs) < 2:
        raise ValueError('Ensemble requires at least 2 member methods')

    has_policy, policy_val = _pick_option(overrides, ensemble_cfg, 'ensemble_policy', 'policy')
    if not has_policy:
        has_policy, policy_val = _pick_option(None, config, 'ensemble_policy')
    policy = str(policy_val).strip() if has_policy and str(policy_val).strip() else 'majority'

    has_min, min_val = _pick_option(overrides, ensemble_cfg, 'ensemble_min_votes', 'min_votes')
    if not has_min:
        has_min, min_val = _pick_option(None, config, 'ensemble_min_votes')
    min_votes = int(min_val) if has_min and type(min_val) is int else (
        min_val if has_min else 0
    )

    has_req, req_val = _pick_option(
        overrides, ensemble_cfg, 'ensemble_require_all_valid', 'require_all_valid'
    )
    if not has_req:
        has_req, req_val = _pick_option(None, config, 'ensemble_require_all_valid')
    require_all_valid = bool(req_val) if has_req else False

    shared_overrides = {
        k: v for k, v in (overrides or {}).items()
        if k not in _ENSEMBLE_ONLY_KEYS and k != 'weights'
    }

    members = []
    try:
        for spec in specs:
            member_method = str(spec['method']).strip()
            member_config = dict(config)
            member_config.update({k: v for k, v in spec.items() if k != 'method'})
            member_overrides = dict(shared_overrides)
            # 멤버 개별 설정은 상위 오버라이드보다 우선 적용(특히 weights 등)
            for key, value in spec.items():
                if key in SUPPORTED_MODEL_OVERRIDES and key not in _ENSEMBLE_ONLY_KEYS:
                    member_overrides[key] = value
            members.append(
                _build_single_method(
                    member_method,
                    member_config,
                    roi,
                    model_dir,
                    explicit_method=member_method,
                    overrides=member_overrides or None,
                )
            )
    except Exception:
        for created in members:
            close = getattr(created, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise

    return EnsembleBedExitMethod(
        members,
        policy=policy,
        min_votes=min_votes,
        require_all_valid=require_all_valid,
    )


def create_method(config_path, method_name=None, overrides: dict | None = None):
    """설정에 선택된 판단 모델(또는 선택적 앙상블) 객체 하나를 생성한다.

    운영에서는 ``method_name``을 생략해 JSON의 ``method``를 사용한다. 여러 모델을 같은
    조건에서 비교하는 연구 코드만 ``method_name``을 전달해 선택값을 덮어쓴다.
    ``overrides``에 지정한 모델 파라미터는 JSON 설정과 v1.3 기본값보다 우선한다.
    """
    if not config_path:
        raise ValueError('Set method_config')

    if overrides is not None:
        if not isinstance(overrides, dict):
            raise TypeError('overrides must be a dict or None')
        unknown = sorted(set(overrides) - SUPPORTED_MODEL_OVERRIDES)
        if unknown:
            raise ValueError(f'Unknown model override key(s): {unknown}')

    config, roi, model_dir = read_config(config_path)

    # 프레임 회전이 설정된 경우, 이미 회전된 ROI가 아니라면 Bed ROI도 함께 회전한다.
    has_rot, raw_angle = _pick_option(overrides, config, 'frame_rotate_ccw')
    angle_ccw = validate_rotation_angle(0 if not has_rot else raw_angle)
    has_already, raw_already = _pick_option(overrides, config, 'roi_already_rotated')
    roi_already_rotated = bool(raw_already) if has_already else False
    if angle_ccw != 0 and not roi_already_rotated:
        roi = rotate_roi_ccw(roi, angle_ccw)

    has_ensemble_override = bool(overrides and overrides.get('ensemble_methods'))
    name = method_name if method_name is not None else config.get('method')
    if name == 'ensemble' or (method_name is None and has_ensemble_override):
        return _build_ensemble_method(config, roi, model_dir, overrides)

    if name not in SUPPORTED_METHODS:
        raise ValueError(f'Unknown method {name!r}; choose from {BUILTIN_METHODS}')

    return _build_single_method(
        name, config, roi, model_dir, explicit_method=method_name, overrides=overrides
    )
