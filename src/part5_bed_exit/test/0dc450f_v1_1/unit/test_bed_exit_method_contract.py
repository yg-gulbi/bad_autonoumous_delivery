"""단위 테스트: 판단 객체의 공통 ``judge(frame)`` 계약을 검증한다.

대상 코드는 ``BedExitMethod``와 ``validate_bed_exit_method()``이다. 실제 모델 없이
가짜 객체를 사용해 추상 클래스 직접 생성 거부와 호출 가능한 ``judge`` 보유 여부를
확인한다.
"""

from types import SimpleNamespace

import pytest

from part5_bed_exit.bed_exit_method import (
    BedExitMethod,
    validate_bed_exit_method,
)


def test_abstract_method_cannot_be_instantiated():
    """``judge``를 구현하지 않은 추상 판단 객체 생성을 거부한다."""
    with pytest.raises(TypeError):
        BedExitMethod()


def test_validate_accepts_instance_with_callable_judge():
    """호출 가능한 ``judge``를 가진 인스턴스만 판단 객체로 인정한다."""
    detector = SimpleNamespace(judge=lambda frame: False)
    assert validate_bed_exit_method(detector) is detector


@pytest.mark.parametrize(
    'invalid',
    [
        object(),
        SimpleNamespace(judge=False),
        type('DetectorClass', (), {'judge': lambda self, frame: False}),
    ],
)
def test_validate_rejects_invalid_targets(invalid):
    """클래스 자체와 ``judge``가 없거나 호출 불가능한 객체를 거부한다."""
    with pytest.raises(TypeError):
        validate_bed_exit_method(invalid)
