"""Operator decision cards; choices can be consumed only while the card is open."""
from uuid import uuid4

OPTIONS = {
    "BED_MONITOR_LOST": {"hold": "계속 정지", "bypass": "감시 없이 운행"},
    "BLOCKED": {"retry": "치웠음, 다시 출발", "return": "되돌아가기", "hold": "임무 보류"},
    "NAV_FAILED": {"retry": "다시 시도", "return": "되돌아가기", "cancel": "취소"},
    "MOVE_REJECTED": {"retry": "다시 보내기", "cancel": "취소"},
    "STOP_MISMATCH": {"recheck": "다시 확인", "skip": "건너뛰기", "cancel": "취소"},
}


class Decisions:
    def __init__(self, record):
        self.cards = {}
        self.record = record

    def open(self, kind, robot, mission_id, reason, now, *, targets=None):
        robots = list(targets) if targets is not None else [robot]
        for card in self.cards.values():
            if card["kind"] == kind and card["robots"] == robots:
                return card
        card_id = f"K-{uuid4().hex}"
        options = OPTIONS[kind]
        card = {"v": 1, "card_id": card_id, "kind": kind, "robots": robots, "title": kind,
                "reason": reason, "options": [{"id": k, "label": v} for k, v in options.items()],
                "recommended": None, "timeout_s": None, "default": None, "created_at": now}
        self.cards[card_id] = card
        self.record("CARD_OPEN", now, reason, robot=robot, mission_id=mission_id, card_id=card_id, card=card)
        return card

    def validate(self, card_id, choice):
        card = self.cards.get(card_id)
        if card is None:
            raise ValueError("E-409: 이미 닫힌 카드입니다.")
        if choice not in OPTIONS[card["kind"]]:
            raise ValueError("E-400: 카드 선택지가 아닙니다.")
        return card

    def close(self, card_id, now, reason, *, choice=None, by=None):
        card = self.cards.pop(card_id, None)
        if card:
            self.record("CARD_CLOSED", now, reason, robot=next(iter(card["robots"]), None), card_id=card_id,
                        choice=choice, by=by)

    def resolve(self, robot, now, kinds=None, *, except_id=None):
        for card in list(self.cards.values()):
            if card["kind"] == "BED_MONITOR_LOST":
                continue  # A camera outage lasts independently of any particular mission.
            if card["card_id"] != except_id and robot in card["robots"] and (kinds is None or card["kind"] in kinds):
                self.close(card["card_id"], now, "resolved")
