"""Method-independent five-frame bed-exit state."""

from collections import deque


class BedExitState:
    """Keep raw frame decisions separately from the published state."""

    def __init__(self):
        # 최근 5프레임만 유지하는 고정 길이 링 버퍼 (5프레임 미만일 때는 False 유지)
        self.history = deque(maxlen=5)
        self.unsafe = False

    def update(self, frame_unsafe):
        # bool이 int의 하위 타입이므로 isinstance 대신 type()으로 0/1이나 np.bool_ 혼입을 차단한다.
        if type(frame_unsafe) is not bool:
            raise TypeError('The method must return a Python bool (True = unsafe)')
        self.history.append(frame_unsafe)
        # 최소 5프레임이 누적된 뒤 최근 5회 중 True(unsafe)가 3회 이상이면 이탈로 판정한다.
        self.unsafe = len(self.history) == 5 and sum(self.history) >= 3
        return self.unsafe
