"""Starter template class for teammate-implemented bed-exit methods."""

from part5_bed_exit.bed_exit_method import BedExitMethod


class BedExitMethodTemplate(BedExitMethod):
    """Copy or subclass this template to implement a concrete bed-exit detector."""

    def __init__(self) -> None:
        """Initialize model weights, bed ROI, or per-detector state here."""
        # 예: YOLO 모델 로드, 침대 ROI 좌표 설정 등 (노드 실행 시 1회 호출)

    def judge(self, frame) -> bool:
        """Analyze one BGR numpy image frame and return True (unsafe) or False (safe)."""
        # 주의: np.bool_이 아니라 반드시 순수 Python bool(True/False)을 반환해야 함.
        # 미구현 상태로 실행해도 거짓 정상(False)을 발행하지 않도록 예외로 차단한다.
        raise NotImplementedError('Implement bed-exit judgment and return Python bool')

    def close(self) -> None:
        """Release model or external resources on node shutdown if needed."""
        # 종료 시 해제가 필요한 자원이 없으면 이 메서드는 생략해도 된다.
