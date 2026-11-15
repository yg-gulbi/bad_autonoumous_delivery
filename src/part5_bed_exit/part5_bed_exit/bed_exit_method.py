"""Base interface and dynamic object loader for bed-exit judgment methods."""

from abc import ABC, abstractmethod
from importlib import import_module
import inspect


class BedExitMethod(ABC):
    """Common interface for replaceable frame-level bed-exit detectors."""

    @abstractmethod
    def judge(self, frame) -> bool:
        """Return True when the BGR frame is unsafe (bed exit), False when safe."""
        # 팀원이 구현할 모든 판단 클래스는 반드시 이 시그니처와 Python bool 반환을 지켜야 함.

    def close(self) -> None:
        """Release optional resources held by the detector instance."""
        # 모델 가중치, 파일 핸들 등을 정리할 클래스만 선택적으로 오버라이드.


def validate_bed_exit_method(detector):
    """Verify that an already-selected detector object exposes judge(frame)."""
    # 클래스 자체가 아니라 생성 완료된 '객체(인스턴스)'이고 judge 메서드를 호출할 수 있는지 검사.
    if inspect.isclass(detector) or not callable(getattr(detector, 'judge', None)):
        raise TypeError(
            'Selected method must be an instance (or class producing an instance) '
            'with a callable judge(frame) method'
        )
    return detector


def load_bed_exit_method(method_path: str):
    """Load a detector object from 'module:ClassName' or 'module:object_name'."""
    # '패키지.모듈:이름' 형식을 분리해 대상 심볼을 동적으로 가져온다.
    module_name, separator, symbol_name = method_path.partition(':')
    if not separator or not module_name or not symbol_name:
        raise ValueError("method must have the form 'module:ClassName' or 'module:object_name'")
    target = getattr(import_module(module_name), symbol_name)
    # 클래스가 지정되면 무인자 생성자 cls()로 객체를 만들고, 이미 만들어진 객체면 그대로 사용한다.
    detector = target() if inspect.isclass(target) else target
    return validate_bed_exit_method(detector)
