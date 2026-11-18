"""침대 이탈 판단 메서드의 공통 계약을 정의하는 파일.

모든 판단 객체는 ``judge(frame) -> bool`` 인터페이스를 제공해야 한다. 이 파일은
추상 인터페이스와 테스트 주입 객체의 최소 계약 검사만 담당한다.
"""

from abc import ABC, abstractmethod
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
