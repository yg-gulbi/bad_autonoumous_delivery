"""침대 이탈 판단 메서드의 공통 계약과 동적 로더를 정의하는 파일.

모든 판단 객체는 ``judge(frame) -> bool`` 인터페이스를 제공해야 한다. 이 파일은
추상 인터페이스, 최소 계약 검사와 ``module:ClassOrObject`` 런타임 로딩을 담당한다.
"""

from abc import ABC, abstractmethod
from importlib import import_module
import inspect


class BedExitMethod(ABC):
    """교체 가능한 프레임 단위 침대 이탈 판단 객체의 추상 인터페이스."""

    @abstractmethod
    def judge(self, frame) -> bool:
        """BGR 프레임이 이탈 위험이면 ``True``, 정상이면 ``False``를 반환한다."""
        # 팀원이 구현할 모든 판단 클래스는 반드시 이 시그니처와 Python bool 반환을 지켜야 함.

    def close(self) -> None:
        """판단 객체가 보유한 선택적 모델·파일 자원을 해제한다."""
        # 모델 가중치, 파일 핸들 등을 정리할 클래스만 선택적으로 오버라이드.


def validate_bed_exit_method(detector):
    """테스트 주입 객체가 호출 가능한 ``judge(frame)``를 제공하는지 검사한다.

    ``BedExitNode(detector=...)``로 단위 테스트 대역을 넣었을 때 최소 인터페이스만
    확인하고 같은 객체를 반환한다.
    """
    # 클래스 자체가 아니라 생성 완료된 '객체(인스턴스)'이고 judge 메서드를 호출할 수 있는지 검사.
    if inspect.isclass(detector) or not callable(getattr(detector, 'judge', None)):
        raise TypeError(
            'Selected method must be an instance (or class producing an instance) '
            'with a callable judge(frame) method'
        )
    return detector


def load_bed_exit_method(
    method_path: str = '',
    config_path: str = '',
    overrides: dict | None = None,
):
    """설정 기반 내장 메서드 또는 ``module:ClassOrObject`` 대상을 생성한다.

    ``method_path``가 비어 있으면 JSON의 ``method``를 사용한다. 내장 메서드 이름은
    동일한 JSON에서 ROI와 모델 경로를 읽고, ``overrides``가 있으면 내장 모델 파라미터를
    덮어쓴다. 외부 클래스는 무인자 생성자로 만든다.
    """
    from part5_bed_exit.pretrained import BUILTIN_METHODS, create_method

    has_overrides = bool(overrides)

    if not method_path:
        if not config_path:
            raise ValueError('Set method or method_config')
        detector = (
            create_method(config_path, overrides=overrides)
            if has_overrides
            else create_method(config_path)
        )
        return validate_bed_exit_method(detector)

    if method_path in BUILTIN_METHODS:
        if not config_path:
            raise ValueError(
                'Built-in methods require method_config pointing to an ROI JSON'
            )
        detector = (
            create_method(config_path, method_path, overrides=overrides)
            if has_overrides
            else create_method(config_path, method_path)
        )
        return validate_bed_exit_method(detector)

    if config_path:
        raise ValueError('method_config applies only to built-in method names')
    if has_overrides:
        raise ValueError('Model parameter overrides apply only to built-in method names')

    module_name, separator, symbol_name = method_path.partition(':')
    if not separator or not module_name or not symbol_name:
        raise ValueError(
            "method must be a built-in name or have the form "
            "'module:ClassName' or 'module:object_name'"
        )

    target = getattr(import_module(module_name), symbol_name)
    detector = target() if inspect.isclass(target) else target
    return validate_bed_exit_method(detector)
