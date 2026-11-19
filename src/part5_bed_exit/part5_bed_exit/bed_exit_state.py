"""AI 모델과 무관하게 최근 5프레임의 이탈 판단을 다수결하는 파일.

입력은 각 프레임에 대한 순수 Python ``bool``이며 ``True``는 이탈 위험을 뜻한다.
최근 5개 값이 모두 모인 뒤 그중 3개 이상이 ``True``일 때만 최종 상태를
``True``로 반환한다. ROS 발행 자체는 이 파일이 아니라 ``bed_exit_node.py``가 맡는다.

System Flowchart v5의 ``5회 프레임 결과 집계``와 ``3회 이상 이탈 여부``는 기존
``BedExitState.update()``에 대응한다.
"""

from collections import deque


class BedExitState:
    """프레임별 원시 판단과 최종 발행 상태를 분리해 보관하는 투표 상태 객체."""

    def __init__(self):
        """최대 5개 판단을 담는 빈 이력과 초기 안전 상태를 만든다."""
        # 최근 5프레임만 유지하는 고정 길이 링 버퍼 (5프레임 미만일 때는 False 유지)
        self.history = deque(maxlen=5)
        self.unsafe = False

    def update(self, frame_unsafe):
        """순서도의 최근 5프레임 중 3회 이상 이탈 다수결을 수행한다."""
        # [PART05-V5-09 5프레임 다수결]
        # 이전: bed_exit_core.BedExitMethod.judge()
        # 다음: bed_exit_node.BedExitNode.process_frame()의 publisher.publish()
        # bool이 int의 하위 타입이므로 isinstance 대신 type()으로 0/1이나 np.bool_ 혼입을 차단한다.
        if type(frame_unsafe) is not bool:
            raise TypeError('The method must return a Python bool (True = unsafe)')
        # [순서도: 5회 프레임 결과 집계] 최대 길이 5인 deque에 현재 판정을 추가한다.
        self.history.append(frame_unsafe)
        # [순서도: 3회 이상 이탈 여부] 5개가 모두 모이고 True가 3개 이상일 때만 이탈이다.
        self.unsafe = len(self.history) == 5 and sum(self.history) >= 3
        return self.unsafe
