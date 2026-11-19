"""단위 테스트: ``bed_exit_state.py``의 5프레임 다수결만 검증한다.

대상 코드는 ``BedExitState.update()``이다. 가능한 5프레임 조합 32개, 오래된 값의
만료, 재감지와 비-``bool`` 입력 거부를 검사한다. 모델·카메라·ROS는 사용하지 않는다.
"""

from itertools import product

import pytest

from part5_bed_exit.bed_exit_state import BedExitState


def test_all_windows_and_startup():
    """가능한 모든 5프레임 조합이 정확히 3/5 규칙을 따르는지 검증한다."""
    for sequence in product((False, True), repeat=5):
        state = BedExitState()
        assert state.unsafe is False
        for value in sequence[:4]:
            assert state.update(value) is False
        assert state.update(sequence[4]) == (sum(sequence) >= 3)


def test_old_frames_expire_and_retrigger():
    """오래된 판단이 창에서 빠지고 새 3/5 조합으로 재감지되는지 검증한다."""
    state = BedExitState()
    sequence = [True, True, True, False, False, False, True, True, True]
    outputs = [state.update(value) for value in sequence]
    assert outputs == [False, False, False, False, True, False, False, False, True]
    assert len(state.history) == 5


@pytest.mark.parametrize('value', [None, 'unsafe', 1, 0])
def test_invalid_decisions_do_not_change_history(value):
    """순수 bool이 아닌 입력이 예외를 내고 기존 이력을 바꾸지 않는지 검증한다."""
    state = BedExitState()
    with pytest.raises(TypeError):
        state.update(value)
    assert list(state.history) == []
