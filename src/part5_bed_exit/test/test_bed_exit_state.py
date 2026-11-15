"""Check startup, all five-frame combinations, and window expiration."""

from itertools import product

import pytest

from part5_bed_exit.bed_exit_state import BedExitState


def test_all_windows_and_startup():
    for sequence in product((False, True), repeat=5):
        state = BedExitState()
        assert state.unsafe is False
        for value in sequence[:4]:
            assert state.update(value) is False
        assert state.update(sequence[4]) == (sum(sequence) >= 3)


def test_old_frames_expire_and_retrigger():
    state = BedExitState()
    sequence = [True, True, True, False, False, False, True, True, True]
    outputs = [state.update(value) for value in sequence]
    assert outputs == [False, False, False, False, True, False, False, False, True]
    assert len(state.history) == 5


@pytest.mark.parametrize('value', [None, 'unsafe', 1, 0])
def test_invalid_decisions_do_not_change_history(value):
    state = BedExitState()
    with pytest.raises(TypeError):
        state.update(value)
    assert list(state.history) == []
