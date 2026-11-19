"""Version 1 String/JSON payloads exchanged with PART 01 (BRIEF section 4)."""

import json
from math import isfinite

from ..defaults import default_robot_ids
from .geometry import RESOURCES, ZONE_REGIONS

VERSION = 1
ROBOT_IDS = default_robot_ids()
MISSION_TYPES = ("PATIENT_TRANSPORT", "MED_ROUND", "CALL")
ERROR_CODES = ("E-400", "E-409", "E-423")
NUMBER = (int, float)
NULL = type(None)

COMMAND_FIELDS = {
    "CONFIRM_STEP": ("robot", "mission_id", "result"),
    "CANCEL_MISSION": ("mission_id",),
    "RETRY": ("robot",),
    "PAUSE_ALL": (),
    "RESUME_ALL": (),
    "PAUSE_ROBOT": ("robot",),
    "RESUME_ROBOT": ("robot",),
    "GO_ZONE": ("robot", "zone"),
    "RESOURCE_CLEAR_CONFIRM": ("resource",),
}


class SchemaError(ValueError):
    """Invalid input. The caller can publish ack() on /ui/ack."""

    code = "E-400"

    def __init__(self, message: str, ref: str | None = None):
        super().__init__(message)
        self.ref = ref

    def user_message(self):
        field, sep, detail = str(self).partition(": ")
        if not sep:
            return "입력 형식을 확인하세요."
        labels = {"v": "메시지 버전", "cmd": "명령", "type": "임무 종류", "level": "응급도",
                  "by": "조작자 이름", "requested_by": "요청자", "result": "확인 결과", "robot": "로봇",
                  "pickup": "출발지", "destination": "목적지", "chart_no": "차트번호", "item": "물품",
                  "stops": "정류장", "zone": "구역", "resource": "구간", "choice": "선택지"}
        label = labels.get(field.split(".")[-1], "입력값")
        return f"{label} 항목을 확인하세요."

    def ack(self) -> dict:
        return {
            "v": VERSION, "ref": self.ref, "ok": False, "code": self.code,
            "message": self.user_message(), "mission_id": None,
        }


def dumps(payload: dict) -> str:
    """Encode strict JSON while keeping Korean text readable."""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _reject_constant(token: str):
    raise ValueError(f"invalid JSON constant: {token}")


def _read(text: str, ref_key: str | None = None) -> tuple[dict, str | None]:
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SchemaError("유효한 JSON 객체가 필요합니다") from exc
    if not isinstance(data, dict):
        raise SchemaError("유효한 JSON 객체가 필요합니다")

    received_ref = data.get(ref_key)
    ref = received_ref if isinstance(received_ref, str) and received_ref.strip() else None
    if type(data.get("v")) is not int or data["v"] != VERSION:
        raise SchemaError("v: JSON 버전은 정수 1이어야 합니다", ref)
    return data, ref


def _fields(data: dict, spec: dict, *, ref: str | None = None, path: str = ""):
    for key, expected in spec.items():
        types = expected if isinstance(expected, tuple) else (expected,)
        if key not in data or type(data[key]) not in types:
            raise SchemaError(f"{path}{key}: 필수 필드가 없거나 형식이 올바르지 않습니다", ref)
        value = data[key]
        if isinstance(value, float) and not isfinite(value):
            raise SchemaError(f"{path}{key}: 유한한 수가 필요합니다", ref)


def _texts(data: dict, keys, ref: str | None, path: str = ""):
    _fields(data, dict.fromkeys(keys, str), ref=ref, path=path)
    for key in keys:
        if not data[key].strip():
            raise SchemaError(f"{path}{key}: 비어 있지 않은 문자열이 필요합니다", ref)


def _choice(value, choices, path: str, ref: str | None = None):
    if not isinstance(value, str) or value not in choices:
        raise SchemaError(f"{path}: 허용된 값은 {', '.join(choices)}입니다", ref)


def _level(data: dict, *, ref: str | None = None, path: str = ""):
    _fields(data, {"level": int}, ref=ref, path=path)
    if not 1 <= data["level"] <= 3:
        raise SchemaError(f"{path}level: 1~3 정수가 필요합니다", ref)


def parse_mission_req(text: str) -> dict:
    data, ref = _read(text, "req_id")
    _texts(data, ("req_id", "type", "requested_by"), ref)
    _choice(data["type"], MISSION_TYPES, "type", ref)
    _level(data, ref=ref)

    if data["type"] == "PATIENT_TRANSPORT":
        _fields(data, {"patient": dict}, ref=ref)
        _texts(data["patient"], ("chart_no",), ref, "patient.")
        if "name" in data["patient"]:
            _fields(data["patient"], {"name": str}, ref=ref, path="patient.")
            if not data["patient"]["name"].strip():
                data["patient"]["name"] = ""
        for key in ("pickup", "destination"):
            _choice(data.get(key), ZONE_REGIONS, key, ref)
    elif data["type"] == "CALL":
        _choice(data.get("destination"), ZONE_REGIONS, "destination", ref)
        if "item" in data:
            _texts(data, ("item",), ref)
    else:
        _fields(data, {"stops": list}, ref=ref)
        if not 1 <= len(data["stops"]) <= 6:
            raise SchemaError("stops: 정류장이 1~6개 필요합니다", ref)
        for i, stop in enumerate(data["stops"]):
            path = f"stops[{i}]."
            if not isinstance(stop, dict):
                raise SchemaError(f"stops[{i}]: 객체가 필요합니다", ref)
            _texts(stop, ("zone", "item"), ref, path)
            _choice(stop["zone"], ZONE_REGIONS, f"{path}zone", ref)
    if "requesting_department" in data:
        _texts(data, ("requesting_department",), ref)
    if data["type"] == "PATIENT_TRANSPORT" and "tag_id" in data["patient"]:
        _texts(data["patient"], ("tag_id",), ref)
    return data


def parse_operator_cmd(text: str, *, robots=None) -> dict:
    data, ref = _read(text, "cmd_id")
    _texts(data, ("cmd_id", "by", "cmd"), ref)
    command = data["cmd"]
    _choice(command, COMMAND_FIELDS, "cmd", ref)
    fields = COMMAND_FIELDS[command]
    _texts(data, fields, ref)
    if robots is None and "robot" in fields:
        robots = ROBOT_IDS
    choices_by_field = {
        "robot": robots, "zone": ZONE_REGIONS, "result": ("OK", "MISMATCH"), "resource": RESOURCES,
    }
    for field in fields:
        if field in choices_by_field:
            _choice(data[field], choices_by_field[field], field, ref)
    return data


def parse_decision(text: str) -> dict:
    data, ref = _read(text, "card_id")
    _texts(data, ("card_id", "choice", "by"), ref)
    return data


def parse_ack(text: str) -> dict:
    data, _ = _read(text, "ref")
    _fields(data, {
        "ref": (str, NULL), "ok": bool, "code": (str, NULL),
        "message": str, "mission_id": (str, NULL),
    })
    if data["ok"]:
        _texts(data, ("ref",), None)
        _fields(data, {"code": NULL})
    else:
        _choice(data["code"], ERROR_CODES, "code", data["ref"])
    return data


def parse_fleet_state(text: str) -> dict:
    data, _ = _read(text)
    _fields(data, {
        "stamp": NUMBER, "robots": dict, "resources": dict, "missions": list,
        "open_cards": list, "bed_exit": dict, "kpi": dict,
    })
    for robot_id, robot in data["robots"].items():
        path = f"robots.{robot_id}."
        if not isinstance(robot, dict):
            raise SchemaError(f"robots.{robot_id}: 객체가 필요합니다")
        _fields(robot, {
            "label": str, "ns": str, "link_ok": bool, "state": str, "part4_state": (str, NULL),
            "region": (str, NULL), "zone": (str, NULL), "pose": (dict, NULL),
            "battery_pct": (*NUMBER, NULL), "mission_id": (str, NULL), "leg": (dict, NULL),
            "wait_reason": (str, NULL), "progress_pct": (*NUMBER, NULL), "remaining_m": (*NUMBER, NULL),
            "stall_s": NUMBER, "paused_by": (str, NULL),
        }, path=path)
        if "obstacle" in robot and robot["obstacle"] is not None:
            observation = robot["obstacle"]
            _fields(robot, {"obstacle": dict}, path=path)
            _fields(observation, {"kind": str, "at": NUMBER, "labels": list, "detections": list}, path=path+"obstacle.")
            _choice(observation["kind"], ("PERSON", "OBJECT", "NONE", "UNKNOWN"), path+"obstacle.kind")
            for detection in observation["detections"]:
                if not isinstance(detection, dict):
                    raise SchemaError("탐지는 객체여야 합니다")
                _fields(detection, {"label": str, "confidence": NUMBER})
                if detection.get("pose") is not None:
                    _fields(detection, {"pose": dict})
                    _fields(detection["pose"], {"x": NUMBER, "y": NUMBER})
        if "state_since" in robot:
            _fields(robot, {"state_since": NUMBER}, path=path)
        if "route" in robot:
            _fields(robot, {"route": list}, path=path)
            for point in robot["route"]:
                if (not isinstance(point, list) or len(point) != 2 or
                        any(type(n) not in NUMBER or not isfinite(n) for n in point)):
                    raise SchemaError(f"{path}route: map 좌표 쌍이 필요합니다")
        if robot["pose"] is not None:
            _fields(robot["pose"], {"x": NUMBER, "y": NUMBER}, path=f"{path}pose.")
        if robot["leg"] is not None:
            for key in ("from", "to"):
                _choice(robot["leg"].get(key), ZONE_REGIONS, f"{path}leg.{key}")

    _fields(data["resources"], dict.fromkeys(RESOURCES, dict), path="resources.")
    for name in RESOURCES:
        resource = data["resources"][name]
        path = f"resources.{name}."
        _fields(resource, {
            "state": str, "capacity": int, "holders": list, "holder": (str, NULL),
            "blocked": bool, "queue": list, "give_way": (dict, NULL),
        }, path=path)
        if "queue_detail" in resource:
            _fields(resource, {"queue_detail": list, "reason": (str, NULL), "decided_at": (*NUMBER, NULL)}, path=path)
            for entry in resource["queue_detail"]:
                if not isinstance(entry, dict):
                    raise SchemaError(f"{path}queue_detail: 로봇별 대기 정보가 필요합니다")
                _fields(entry, {"robot": str, "mission_id": (str, NULL), "since": NUMBER}, path=path)
                _level(entry, path=path)
        capacities = (1, 2) if name == "CORRIDOR" else (1,)
        if resource["capacity"] not in capacities:
            raise SchemaError(f"{path}capacity: 유효하지 않은 정원입니다")
        holders = resource["holders"]
        if (any(not isinstance(r, str) or not r.strip() for r in holders)
                or len(set(holders)) != len(holders) or len(holders) > resource["capacity"]):
            raise SchemaError(f"{path}holders: 정원 이내의 고유 로봇 ID 목록이 필요합니다")
        if resource["holder"] != next(iter(holders), None):
            raise SchemaError(f"{path}holder: 첫 번째 보유 로봇과 일치해야 합니다")
        if resource["state"] != "UNKNOWN" and (resource["state"] == "FREE") != (not holders):
            raise SchemaError(f"{path}state: 점유 상태와 보유 목록이 일치해야 합니다")
        give_way = resource["give_way"]
        if give_way is not None:
            _fields(give_way, {"yielding": str, "to": str, "since": NUMBER}, path=f"{path}give_way.")
            if (resource["capacity"] != 2 or give_way["yielding"] == give_way["to"]
                    or {give_way["yielding"], give_way["to"]} != set(holders)):
                raise SchemaError(f"{path}give_way: 복도를 공유하는 두 로봇이 필요합니다")
        _choice(resource["state"], ("FREE", "GRANTED", "OCCUPIED", "UNKNOWN"), f"{path}state")
        for i, entry in enumerate(resource["queue"]):
            entry_path = f"{path}queue[{i}]."
            if not isinstance(entry, dict):
                raise SchemaError(f"{path}queue[{i}]: 객체가 필요합니다")
            _fields(entry, {"robot": str, "since": NUMBER}, path=entry_path)
            _level(entry, path=entry_path)

    for i, mission in enumerate(data["missions"]):
        path = f"missions[{i}]."
        if not isinstance(mission, dict):
            raise SchemaError(f"missions[{i}]: 객체가 필요합니다")
        _fields(mission, {
            "mission_id": str, "type": str, "robot": (str, NULL), "status": str,
            "stops": list, "stop_index": int,
        }, path=path)
        if "created_at" in mission:
            _fields(mission, {"patient": (dict,NULL), "items": list, "created_at": NUMBER, "finished_at": (*NUMBER,NULL),
                              "requested_by": (str,NULL), "wait_reason": (str,NULL)}, path=path)
        _choice(mission["type"], MISSION_TYPES, f"{path}type")
        if mission["robot"] is None:
            if mission["status"] not in ("QUEUED", "CANCELED"):
                raise SchemaError(f"{path}robot: 배정 전 대기 임무만 null일 수 있습니다")
            if mission["status"] == "QUEUED":
                _texts(mission, ("wait_reason",), None, path)
        _level(mission, path=path)
        for j, stop in enumerate(mission["stops"]):
            _choice(stop, ZONE_REGIONS, f"{path}stops[{j}]")

    decision = data.get("last_decision")
    if decision is not None:
        if not isinstance(decision, dict):
            raise SchemaError("last_decision: 판단 이벤트가 필요합니다")
        _fields(decision, {"type": str, "ts": NUMBER, "robot": (str,NULL), "detail": dict})
        _decision_detail(decision["detail"])
    if any(not isinstance(card, str) for card in data["open_cards"]):
        raise SchemaError("open_cards: 카드 ID 목록이 필요합니다")
    _fields(data["bed_exit"], {"value": (bool, NULL), "since": (*NUMBER, NULL)}, path="bed_exit.")
    if "monitor" in data["bed_exit"]:
        monitor = data["bed_exit"]["monitor"]
        if not isinstance(monitor, dict):
            raise SchemaError("bed_exit.monitor: 감시 상태 객체가 필요합니다")
        _fields(monitor, {"state": str, "last_rx": (*NUMBER, NULL), "rate_hz": NUMBER,
                          "since": NUMBER, "bypass": bool}, path="bed_exit.monitor.")
        _choice(monitor["state"], ("NEVER", "OK", "WARN", "LOST"), "bed_exit.monitor.state", None)
    _fields(data["kpi"], {
        "hold_count": int, "deadlock_count": int, "deadlock_resolve_s_avg": (*NUMBER, NULL),
        "pause_count": int,
    }, path="kpi.")
    return data


def parse_decision_req(text: str) -> dict:
    data, ref = _read(text, "card_id")
    _texts(data, ("card_id", "kind", "title", "reason"), ref)
    _fields(data, {
        "robots": list, "options": list, "recommended": (str, NULL), "default": (str, NULL),
        "timeout_s": (*NUMBER, NULL), "created_at": NUMBER,
    }, ref=ref)
    if any(not isinstance(robot, str) for robot in data["robots"]):
        raise SchemaError("robots: 로봇 ID 목록이 필요합니다", ref)
    option_ids = []
    for i, option in enumerate(data["options"]):
        if not isinstance(option, dict):
            raise SchemaError(f"options[{i}]: 객체가 필요합니다", ref)
        _texts(option, ("id", "label"), ref, f"options[{i}].")
        option_ids.append(option["id"])
    for key in ("recommended", "default"):
        if data[key] is not None:
            _choice(data[key], option_ids, key, ref)
    return data


def _decision_detail(detail):
    _fields(detail, {"rule": str, "state_before": str, "contenders": list, "reason": str})
    for contender in detail["contenders"]:
        if not isinstance(contender, dict):
            raise SchemaError("contenders: 경쟁 로봇 정보가 필요합니다")
        _fields(contender, {"robot": str, "level": int, "mission_id": (str,NULL), "type": (str,NULL),
                            "requested_at": NUMBER})


def parse_event(text: str) -> dict:
    data, _ = _read(text)
    _texts(data, ("type",), None)
    _fields(data, {"ts": NUMBER, "robot": (str, NULL), "mission_id": (str, NULL), "detail": dict})
    if data["type"] in ("RESOURCE_GRANT", "RESOURCE_HOLD", "CORRIDOR_YIELD_START") and "rule" in data["detail"]:
        _decision_detail(data["detail"])
    return data
