"""목적: ``bed_exit_state.py``의 ``BedExitState.update()``만 검증한다.

모델·카메라·ROS 없이 Bool 입력, 5프레임 창, 3회 이상 True 규칙과 입력 타입
검증을 확인한다.
"""

from itertools import product

import pytest

from part5_bed_exit.bed_exit_state import BedExitState


def test_update_applies_three_of_five_rule_to_every_window():
    for sequence in product((False, True), repeat=5):
        state = BedExitState()
        for value in sequence[:4]:
            assert state.update(value) is False
        assert state.update(sequence[4]) == (sum(sequence) >= 3)


def test_update_expires_oldest_value_after_five_frames():
    state = BedExitState()
    sequence = [True, True, True, False, False, False, True, True, True]

    assert [state.update(value) for value in sequence] == [
        False, False, False, False, True, False, False, False, True,
    ]
    assert len(state.history) == 5


@pytest.mark.parametrize('value', [None, 'unsafe', 1, 0])
def test_update_rejects_non_python_bool_without_changing_history(value):
    state = BedExitState()

    with pytest.raises(TypeError):
        state.update(value)

    assert list(state.history) == []
    assert state.unsafe is False
