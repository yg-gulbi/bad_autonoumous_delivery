"""팀원이 새 침대 이탈 판단 메서드를 작성할 때 복사하는 최소 구현 예시.

운영 메서드가 아니라 확장용 골격이다. ``judge(frame)``에 모델 추론을 구현하고
반드시 순수 Python ``bool``을 반환해야 하며, 현재 상태로 호출하면 의도적으로
``NotImplementedError``가 발생한다.
"""

from part5_bed_exit.bed_exit_method import BedExitMethod


class BedExitMethodTemplate(BedExitMethod):
    """구체적인 침대 이탈 판단기를 작성할 때 복사하거나 상속하는 템플릿."""

    def __init__(self) -> None:
        """모델 가중치, 침대 ROI와 판단기별 상태를 초기화할 위치를 보여준다."""
        # 예: YOLO 모델 로드, 침대 ROI 좌표 설정 등 (노드 실행 시 1회 호출)

    def judge(self, frame) -> bool:
        """BGR NumPy 프레임 하나를 분석해 이탈 위험 여부를 Python ``bool``로 반환한다."""
        # 주의: np.bool_이 아니라 반드시 순수 Python bool(True/False)을 반환해야 함.
        # 미구현 상태로 실행해도 거짓 정상(False)을 발행하지 않도록 예외로 차단한다.
        raise NotImplementedError('Implement bed-exit judgment and return Python bool')

    def close(self) -> None:
        """노드 종료 시 모델 또는 외부 자원을 해제할 위치를 보여준다."""
        # 종료 시 해제가 필요한 자원이 없으면 이 메서드는 생략해도 된다.
