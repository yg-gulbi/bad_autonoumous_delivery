"""AI 모델과 무관하게 최근 N프레임의 이탈 판단을 다수결하는 파일.

입력은 각 프레임에 대한 순수 Python ``bool``이며 ``True``는 이탈 위험을 뜻한다.
기본값은 기존 v1.3 계약과 동일하게 최근 5개 값이 모두 모인 뒤 그중 3개 이상이
``True``일 때만 최종 상태를 ``True``로 반환한다. ``window_size``, ``threshold``,
``prefill_safe``를 통해 투표 창 크기·임계치·초기 정상 채움 여부를 조정할 수 있다.
ROS 발행 자체는 이 파일이 아니라 ``bed_exit_node.py``가 맡는다.

System Flowchart v5의 ``5회 프레임 결과 집계``와 ``3회 이상 이탈 여부``는
``BedExitState.update()``에 대응한다.
"""

from collections import deque

DEFAULT_VOTING_WINDOW = 5
DEFAULT_VOTING_THRESHOLD = 3


class BedExitState:
    """프레임별 원시 판단과 최종 발행 상태를 분리해 보관하는 투표 상태 객체."""

    def __init__(
        self,
        window_size: int = DEFAULT_VOTING_WINDOW,
        threshold: int = DEFAULT_VOTING_THRESHOLD,
        prefill_safe: bool = False,
    ):
        """지정한 창 크기·임계치·초기 채움 정책으로 투표 이력과 안전 상태를 만든다."""
        if type(window_size) is not int or window_size < 1:
            raise ValueError('window_size must be an int >= 1')
        if type(threshold) is not int or threshold < 1 or threshold > window_size:
            raise ValueError('threshold must be an int in [1, window_size]')
        if type(prefill_safe) is not bool:
            raise TypeError('prefill_safe must be a Python bool')

        self.window_size = window_size
        self.threshold = threshold
        self.prefill_safe = prefill_safe
        self.history = deque(maxlen=window_size)
        self.unsafe = False
        self.reset()

    def reset(self) -> None:
        """투표 설정은 유지한 채 이력과 최종 상태를 초기 상태로 되돌린다."""
        if self.prefill_safe:
            self.history = deque([False] * self.window_size, maxlen=self.window_size)
        else:
            self.history = deque(maxlen=self.window_size)
        self.unsafe = False

    def update(self, frame_unsafe: bool) -> bool:
        """순서도의 최근 N프레임 중 K회 이상 이탈 다수결을 수행한다."""
        # [PART05-V5-09 N프레임 다수결]
        # 이전: bed_exit_core.BedExitMethod.judge()
        # 다음: bed_exit_node.BedExitNode.process_frame()의 publisher.publish()
        # bool이 int의 하위 타입이므로 isinstance 대신 type()으로 0/1이나 np.bool_ 혼입을 차단한다.
        if type(frame_unsafe) is not bool:
            raise TypeError('The method must return a Python bool (True = unsafe)')
        # [순서도: N회 프레임 결과 집계] 최대 길이 window_size인 deque에 현재 판정을 추가한다.
        self.history.append(frame_unsafe)
        # [순서도: K회 이상 이탈 여부] 창이 모두 차고 True가 threshold 이상일 때만 이탈이다.
        self.unsafe = (
            len(self.history) == self.window_size
            and sum(self.history) >= self.threshold
        )
        return self.unsafe
