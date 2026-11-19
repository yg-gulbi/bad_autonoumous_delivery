"""Operator decision cards; choices can be consumed only while the card is open."""
from uuid import uuid4

OPTIONS = {
    "BED_MONITOR_LOST": {"hold": "계속 정지", "bypass": "감시 없이 운행"},
    "BLOCKED": {"retry": "치웠음, 다시 출발", "return": "되돌아가기", "hold": "임무 보류"},
    "NAV_FAILED": {"retry": "다시 시도", "return": "되돌아가기", "cancel": "취소"},
    "MOVE_REJECTED": {"retry": "다시 보내기", "cancel": "취소"},
    "STOP_MISMATCH": {"recheck": "다시 확인", "skip": "건너뛰기", "cancel": "취소"},
}


TITLES = {"BED_MONITOR_LOST": "침상 감시 끊김", "BLOCKED": "경로 막힘", "NAV_FAILED": "주행 실패",
          "MOVE_REJECTED": "이동 명령 거절", "STOP_MISMATCH": "확인 불일치", "DEADLOCK": "교착 해소"}
CONFIRM_REASONS = {"OK": "확인 완료", "MISMATCH": "내용 불일치 — 운영자 결정 대기"}
DONE_REASON = "모든 정류장 확인 완료"


def close_reason(closed_by, label):
    return {"selected": f"'{label}' 선택" if label else "운영자가 선택함",
            "timeout": "시간 초과 — 기본 선택으로 진행", "resolved": "상황이 풀려 닫힘"}.get(closed_by, "")


def close_detail(card, closed_by, choice):
    label = next((o["label"] for o in card["options"] if o["id"] == choice), None)
    return dict(reason=close_reason(closed_by, label), closed_by=closed_by, choice_label=label, choice=choice)


class Decisions:
    def __init__(self, record):
        self.cards = {}
        self.record = record

    def open(self, kind, robot, mission_id, reason, now, *, targets=None, obstacle_kind=None, context=None, options=None):
        robots = list(targets) if targets is not None else [robot]
        for card in self.cards.values():
            if card["kind"] == kind and card["robots"] == robots and card.get("context") == context:
                return card
        card_id = f"K-{uuid4().hex}"
        options = options or OPTIONS[kind]
        if kind == "BLOCKED" and not context:
            retry = {"PERSON": "비켜 줌, 다시 출발", "OBJECT": options["retry"]}.get(obstacle_kind, "확인함, 다시 출발")
            options = {**options, "retry": retry}
        card = {"v": 1, "card_id": card_id, "kind": kind, "robots": robots, "title": TITLES[kind],
                "reason": reason, "options": [{"id": k, "label": v} for k, v in options.items()],
                "recommended": None, "timeout_s": None, "default": None, "created_at": now}
        if context:
            card["context"] = context
        if mission_id is not None:
            card["mission_id"] = mission_id
        self.cards[card_id] = card
        self.record("CARD_OPEN", now, reason, robot=robot, mission_id=mission_id, card_id=card_id, card=card)
        return card

    def validate(self, card_id, choice):
        card = self.cards.get(card_id)
        if card is None:
            raise ValueError("E-409: 이미 닫힌 카드입니다.")
        if not any(o["id"] == choice for o in card["options"]):
            raise ValueError("E-400: 카드 선택지가 아닙니다.")
        return card

    def close(self, card_id, now, reason, *, choice=None, by=None):
        card = self.cards.pop(card_id, None)
        if card:
            self.record("CARD_CLOSED", now, **close_detail(card, reason, choice), robot=next(iter(card["robots"]), None), card_id=card_id,
                        mission_id=card.get("mission_id"), by=by)

    def resolve(self, robot, now, kinds=None, *, except_id=None):
        for card in list(self.cards.values()):
            if card["kind"] == "BED_MONITOR_LOST":
                continue  # A camera outage lasts independently of any particular mission.
            if card["card_id"] != except_id and robot in card["robots"] and (kinds is None or card["kind"] in kinds):
                self.close(card["card_id"], now, "resolved")
