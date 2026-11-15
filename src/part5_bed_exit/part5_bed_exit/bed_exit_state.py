"""Method-independent five-frame bed-exit state."""

from collections import deque


class BedExitState:
    """Keep raw frame decisions separately from the published state."""

    def __init__(self):
        self.history = deque(maxlen=5)
        self.unsafe = False

    def update(self, frame_unsafe):
        if type(frame_unsafe) is not bool:
            raise TypeError('The method must return a Python bool (True = unsafe)')
        self.history.append(frame_unsafe)
        self.unsafe = len(self.history) == 5 and sum(self.history) >= 3
        return self.unsafe
