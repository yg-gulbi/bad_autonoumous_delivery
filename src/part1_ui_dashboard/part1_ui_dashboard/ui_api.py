"""Convert monitor form data to the public fleet ICD, without ROS or Flask."""
from collections import Counter
from itertools import count
from math import atan2, cos, degrees, floor, radians, sin
from pathlib import Path
from time import time_ns

import re
import yaml

from .occupancy import BIN_M, Grid, deskew_angle, map_point, render_grid, unmap_point, world_matrix

from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.decisions import CONFIRM_REASONS, DONE_REASON, OPTIONS, close_reason

_sequence = count(1)


def request_id():
    return f"mon-{time_ns() // 1_000_000}-{next(_sequence)}"


def _operator(form):
    value = form.get("operator", "operator")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("조작자 이름을 입력하세요.")
    return value.strip()


def mission(form):
    kind = form.get("type", "CALL")
    level = form.get("level")
    if isinstance(level, str) and level in ("1", "2", "3"):
        level = int(level)
    data = {"v": 1, "req_id": request_id(), "requested_by": _operator(form), "type": kind, "level": level}
    if kind == "CALL":
        data["destination"] = form.get("destination")
        if form.get("item") not in (None, ""):
            data["item"] = form["item"]
    elif kind == "PATIENT_TRANSPORT":
        if form.get("pickup") == form.get("destination"):
            raise ValueError("픽업 구역과 목적지는 달라야 합니다.")
        patient = {"chart_no": form.get("chart_no")}
        if form.get("name"):
            patient["name"] = form["name"]
        if form.get("tag_id"):
            patient["tag_id"] = form["tag_id"]
        data.update(patient=patient,
                    pickup=form.get("pickup"), destination=form.get("destination"))
    elif kind == "MED_ROUND":
        if "stops" in form:
            data["stops"] = form["stops"]
        else:
            item = form.get("item")
            if not isinstance(item, str) or not item.strip():
                raise ValueError("물품 이름을 입력하세요.")
            zones = [form.get("pickup"), form.get("destination")]
            extra = form.get("extra", [])
            if not isinstance(extra, list) or not all(isinstance(z, str) for z in extra):
                raise ValueError("추가 정류장을 확인하세요.")
            zones.extend(extra)
            if any(not isinstance(zone, str) or not zone for zone in zones):
                raise ValueError("정류장 구역을 선택하세요.")
            if len(set(zones)) != len(zones):
                raise ValueError("각 정류장은 다른 구역이어야 합니다.")
            data["stops"] = [{"zone": z, "item": ("수령: " if i == 0 else "인계: ") + item.strip()}
                             for i,z in enumerate(zones)]
            if form.get("department"):
                data["requesting_department"] = form["department"]
    return schemas.parse_mission_req(schemas.dumps(data))


def command(form, *, robots=None, resources=None):
    cmd = form.get("cmd")
    if not isinstance(cmd, str) or cmd not in schemas.COMMAND_FIELDS:
        raise ValueError("관제 명령을 확인하세요.")
    data = {key: form.get(key) for key in schemas.COMMAND_FIELDS[cmd]}
    if cmd in schemas.COMMAND_LEVEL and form.get("level") is not None:
        data["level"] = form["level"]      # GO_ZONE without a level goes at mission.manual_level
    if cmd in schemas.COMMAND_FLAGS:
        data[schemas.COMMAND_FLAGS[cmd]] = form.get(schemas.COMMAND_FLAGS[cmd])   # BED_MONITOR_SET: enabled
    data.update(v=1, cmd=cmd, cmd_id=request_id(), by=_operator(form))
    return schemas.parse_operator_cmd(schemas.dumps(data), robots=robots, resources=resources)


def decision(form):
    data = {"v": 1, "card_id": form.get("card_id"), "choice": form.get("choice"), "by": _operator(form)}
    return schemas.parse_decision(schemas.dumps(data))

STATES = {"IDLE": "대기", "RETURNING": "복귀 중", "SENDING": "출발 준비", "MOVING": "이동 중",
          "HOLD": "진입 전 대기",
          "WAIT_CONFIRM": "확인 대기", "SAFE_PAUSE": "안전 정지", "FAULT": "장애", "OFFLINE": "연결 끊김"}
KINDS = {"PATIENT_TRANSPORT": "환자 이송", "MED_ROUND": "약품 운송", "CALL": "로봇 호출"}
EVENTS = {
    "OBSTACLE_INSPECT": "{robot} 장애물 확인 요청", "OBSTACLE_RESULT": "{robot} 장애물 확인 결과",
    "OBSTACLE_REPORT": "{robot} 장애물 사진 수신", "OBSTACLE_RETRY": "{robot} 장애물 없음 · 자동 재출발",
    "STOP_RESENT": "{robot} 정지 다시 보냄", "STOP_UNCONFIRMED": "{robot} 정지 미확인",
    "ARRIVED_FROM_STATE": "{robot} · {zone} 도착 (상태 보고로 확인)",
    "MISSION_CREATED": "{kind} 요청 등록", "MISSION_ASSIGNED": "{robot} 임무 배정", "MISSION_REJECTED": "임무 등록 거절",
    "LEG_START": "{robot} 구간 출발", "ARRIVE": "{robot} · {zone} 도착", "CONFIRM_STEP": "{zone} 확인 결과",
    "MISSION_DONE": "{robot} 임무 완료", "MISSION_CANCELED": "임무 취소", "RESOURCE_REQUEST": "{robot} 통행 요청",
    "PLAN_UPDATE": "두 로봇 다시 계획", "PLAN_WAIT": "{robot} 계획 순서 대기", "PLAN_YIELD_MOVE": "{robot} 길 비켜 주는 중",
    "LEVEL_CHANGE": "응급도 변경",
    "RESOURCE_ENTER": "{robot} {resource} 진입 확인", "RESOURCE_RELEASE": "{robot} {resource} 통과 완료",
    "RESOURCE_UNKNOWN": "{resource} 점유 확인 불가", "RESOURCE_CLEAR_CONFIRM": "{resource} 현장 확인",
    "ZONE_OCCUPIED_HOLD": "{robot} 출발 대기", "ZONE_OCCUPIED_CLEAR": "{robot} 출발 대기 해제",
    "PROXIMITY_STOP": "{robot} 근접 정지", "PROXIMITY_CLEAR": "{robot} 근접 정지 해제",
    "STALL_SHORT": "{robot} 일시 정체",
    "STALL_LONG": "{robot} 장기 정체", "BLOCKED": "이동 경로 막힘", "UNBLOCKED": "이동 경로 막힘 해제",
    "PAUSE": "{robot} 정지", "RESUME": "{robot} 재개", "RESUME_REJECTED": "재개 요청 거절",
    "BED_MONITOR_WARN": "침상 감시 경고", "BED_MONITOR_LOST": "침상 감시 끊김",
    "BED_MONITOR_BACK": "침상 감시 복구", "BED_MONITOR_BYPASS": "침상 감시 없이 운행",
    "BED_MONITOR_ENABLED": "침상 감시 켬", "BED_MONITOR_DISABLED": "침상 감시 끔",
    "BED_EXIT": "침상 이탈 감지", "BED_EXIT_CLEAR": "침상 이탈 해제", "BUMP": "{robot} 범퍼 감지",
    "MOVE_REJECTED": "{robot} 이동 명령 거절", "NAV_FAILED": "{robot} 주행 실패", "FAULT": "{robot} 장애 발생",
    "CARD_OPEN": "운영자 결정 요청", "CARD_CLOSED": "결정 카드 종료", "LINK_LOST": "{robot} 연결 끊김",
    "LINK_BACK": "{robot} 연결 정상", "WARNING": "관제 주의",
}


DEFAULT_ROBOT_NAMES = {"robot_a": "침상형 이송 로봇", "robot_b": "약품 운송 로봇"}
_SEGMENT_LABELS = {
    "CORRIDOR_TOP": "복도 위쪽",
    "CORRIDOR_BOTTOM": "복도 아래쪽",
    "CROSSROAD": "교차로",
    "HALL_TOP": "위쪽 홀",
    "HALL_BOTTOM": "아래쪽 홀",
}


def resource_label(name, names, segment_labels=None):
    if name == "CORRIDOR":
        return "복도"
    if name in ("ROOM_B", "ROOM_E"):
        return names.get(name[-1], name[-1] + " 구역") + " 출입"
    if segment_labels and name in segment_labels:
        return segment_labels[name]
    if name in _SEGMENT_LABELS:
        return _SEGMENT_LABELS[name]
    if name in ("DOOR_B", "DOOR_E"):
        return names.get(name[-1], name[-1] + " 구역") + " 문"
    if len(name) == 2 and name[0] in "ABCDEF" and name[1] in "12":
        return f"{names.get(name[0], name[0] + ' 구역')} {name[1]}번 자리"
    return names.get(name[-1], name[-1] + " 구역") + " 출입"


def legacy_names(text, robots, names=None):
    for zone, label in (names or {}).items():
        text = text.replace(f"{zone} 구역", label)
    text = text.replace(" (2번 자리)", " 2번 자리")
    for key, label in robots.items():
        old_label = {"robot_a": "Robot A", "robot_b": "Robot B"}.get(key)
        if old_label:
            text = text.replace(old_label, label)
        text = re.sub(re.escape(key) + r"(?:이|가|은|는|의|에(?= 허가))?", lambda _: label, text)
    return text.replace("공용도로", "복도").replace("통로", "복도")


def present_event(event, names, robots=None, card=None, segment_labels=None):
    robots = robots or DEFAULT_ROBOT_NAMES
    d = dict(event["detail"])
    kind, reason = event["type"], d.get("reason") or ""
    zone = names.get(d.get("destination"), "정류장")
    if d.get("slot") == 2 and d.get("destination") in names:
        zone += " 2번 자리"
    robot = robots.get(event.get("robot"), "")
    res_keys = d.get("resources", [])
    resources = [resource_label(n, names, segment_labels) for n in res_keys]
    title = EVENTS.get(kind, "관제 기록").format(robot=robot, zone=zone, resource=" · ".join(resources) or "구간",
                                               kind=KINDS.get(d.get("mission_type"), "이송")).strip()
    if kind == "OBSTACLE_REPORT" and d.get("source") == "yolo":
        title = f"{robot} 주행 중 장애물 감지".strip()
    elif kind == "CONFIRM_STEP":
        result = d.get("result", reason)
        reason = CONFIRM_REASONS.get(result, reason)
        title = zone + (" 확인 완료" if reason == CONFIRM_REASONS["OK"] else " 확인 불일치")
    elif kind == "CARD_CLOSED" and reason in ("selected", "timeout", "resolved"):
        choice = d.get("choice")
        options = (card or {}).get("options", [])
        label = d.get("choice_label") or next((o["label"] for o in options if o["id"] == choice), None)
        label = label or next((options[choice] for options in OPTIONS.values() if choice in options), None)
        reason = close_reason(reason, label)
    elif kind == "MISSION_DONE" and reason == "DONE":
        reason = DONE_REASON
    elif kind == "RESOURCE_REQUEST" and d.get("destination") and any(key in reason for key in robots):
        reason = f"{robot} — {zone}으로 가는 구간 요청"
    elif kind in ("ARRIVE", "LEG_START") and reason.startswith(str(d.get("destination"))):
        reason = zone + (" 도착" if kind == "ARRIVE" else " 이동 시작")
    elif reason in ("MANUAL", "BUMP", "BED_EXIT", "BED_MONITOR_LOST"):
        reason = {"MANUAL": "운영자 안전 정지 요청", "BUMP": "범퍼 충돌", "BED_EXIT": "침상 이탈 감지",
                  "BED_MONITOR_LOST": "침상 감시 끊김"}[reason]
    elif reason == "STATE 수신 상태 변경":
        reason = "로봇 상태 수신 재개" if kind == "LINK_BACK" else "로봇 상태 수신 중단"
    elif kind in ("FAULT", "NAV_FAILED", "MOVE_REJECTED", "WARNING") and re.search(
            r"Nav2|hospital_nav|MISSION_|MOVE|PART 04|ABORTED", reason):
        reason = {"FAULT": "로봇 주행 상태를 확인하세요.", "NAV_FAILED": "목적지까지 주행하지 못했습니다.",
                  "MOVE_REJECTED": "이동 요청을 수락하지 못했습니다.", "WARNING": "로봇 상태 또는 명령 응답을 확인하세요."}[kind]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ .:/-]*", reason):
        reason = ""
    d["reason"] = legacy_names(reason, robots, names)
    return {**event, "title": title, "detail": d}


def event_title(e, names):
    return present_event(e, names)["title"]


def summary(state):
    robots = state["robots"]
    active = [m for m in state["missions"] if m["status"] == "ACTIVE"]
    urgent = [m for m in state["missions"] if m["level"] == 3 and m["status"] in ("ACTIVE", "QUEUED")]
    blocked = {r for res in state["resources"].values() if res["blocked"] for r in res.get("blocked_robots", res["holders"])}
    waits = [r for r, data in robots.items() if r in blocked or data["state"] in ("HOLD", "SAFE_PAUSE", "BLOCKED")]
    return {"online": sum(r["link_ok"] for r in robots.values()), "active": len(active),
            "kinds": {k: sum(m["type"] == k for m in active) for k in KINDS},
            "urgent": [m["mission_id"] for m in urgent], "waiting": waits}


def map_meta(path, rotation="ccw90", deskew="auto", padding_m=.25, floor_plan=None):
    try:
        grid = Grid.read(Path(path))
    except FileNotFoundError:
        grid = None
    if deskew != "auto":
        angle = float(deskew)
    elif grid:
        angle = deskew_angle(grid.wall_centers(), grid.origin)
    elif floor_plan:
        # With just the plan, the first outside wall still supplies the building orientation.
        x1, y1, x2, y2 = floor_plan["walls"][0]
        angle = degrees(atan2(x1-x2, y1-y2))
    else:
        angle = 0.0
    resolution = grid.resolution if grid else .05
    matrix = world_matrix(angle, rotation, resolution)
    width, height, pgm = render_grid(grid, matrix, padding_m) if grid else (0, 0, None)
    # ROS map origin is the bottom-left corner of the cleaned grid, in the map frame.
    ox, oy = unmap_point(matrix, 0, height)
    origin_yaw = atan2(matrix[2], matrix[0])
    return {"available": bool(grid), "floor_plan": floor_plan, "rotation": rotation, "deskew_deg": angle,
            "origin": [ox, oy, origin_yaw], "image_matrix": [1, 0, 0, 1, 0, 0],
            "free_thresh": .196, "occupied_thresh": .65, "negate": 0,
            "matrix": matrix, "resolution": resolution, "width": width, "height": height}, pgm


def _wall_lines(values):
    """Find the four distinct parallel walls of the two-hall, two-room plan."""
    counts = Counter(floor(v/BIN_M + .5) for v in values)
    # Combine adjacent bins across the thickness of a wall, then measure its centre.
    peaks = []
    for k in sorted(counts, key=lambda k: -sum(counts[k+d] for d in (-1, 0, 1))):
        if all(abs(k-prev)*BIN_M > .35 for prev in peaks):
            peaks.append(k)
            if len(peaks) == 4:
                break
    if len(peaks) < 4:
        raise ValueError("평면도의 평행한 벽 네 줄을 찾지 못했습니다.")
    lines = []
    for k in sorted(peaks):
        band = [v for v in values if abs(v - k*BIN_M) <= .10]
        lines.append(sum(band)/len(band))
    return lines


def _door_span(points, wall_x, start, end):
    """Longest opening between measured returns along one corridor wall."""
    occupied = sorted({floor(y/BIN_M) for x, y in points if abs(x-wall_x) <= .10 and start <= y <= end})
    if len(occupied) < 2:
        raise ValueError("복도 벽에서 문을 찾지 못했습니다.")
    a, b = max(zip(occupied, occupied[1:]), key=lambda pair: pair[1]-pair[0])
    if b-a < 3:
        raise ValueError("복도 벽에서 열린 문을 찾지 못했습니다.")
    return (a+1)*BIN_M, b*BIN_M


def measure_floor_plan(map_yaml):
    """Measure this hospital layout from occupied cell centres without using the saved plan."""
    grid = Grid.read(Path(map_yaml))
    points = grid.wall_centers()
    angle = radians(deskew_angle(points, grid.origin))
    c, s = cos(angle), sin(angle)
    points = [(c*x-s*y, s*x+c*y) for x, y in points]
    bottom, room_b, room_e, top = _wall_lines([x for x, y in points])
    right, inner_right, inner_left, left = _wall_lines([y for x, y in points])

    def segment(x1, y1, x2, y2):
        return [round(v, 2) for v in (c*x1+s*y1, -s*x1+c*y1, c*x2+s*y2, -s*x2+c*y2)]

    walls = [segment(top, left, top, right), segment(bottom, left, bottom, right),
             segment(top, left, bottom, left), segment(top, right, bottom, right)]
    for y in (inner_left, inner_right):
        walls.extend([segment(top, y, room_e, y), segment(room_b, y, bottom, y)])
    doors = {}
    for key, x in (("ROOM_E", room_e), ("ROOM_B", room_b)):
        far, near = _door_span(points, x, inner_right, inner_left)
        walls.extend([segment(x, inner_left, x, near), segment(x, far, x, inner_right)])
        doors[key] = segment(x, near, x, far)
    return {"source_map": str(map_yaml), "walls": walls, "doors": doors, "labels": {"corridor": "복도"}}


def measure_main():
    import argparse
    from tempfile import NamedTemporaryFile

    parser = argparse.ArgumentParser(description="점유 격자에서 병원 평면도의 벽과 문을 잽니다.")
    parser.add_argument("--map-yaml", default="main_map/main_maps.yaml")
    source_config = Path("src/part1_ui_dashboard/config")
    parser.add_argument("--output", type=Path,
                        default=source_config/"floor_plan.yaml" if source_config.is_dir() else Path("floor_plan.yaml"))
    args = parser.parse_args()
    result = measure_floor_plan(args.map_yaml)
    # A running UI or a subsequent invocation sees a complete YAML file.
    with NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as temp:
        yaml.safe_dump(result, temp, allow_unicode=True, sort_keys=False, default_flow_style=None)
    Path(temp.name).replace(args.output)
    print(f"{args.output} · 벽 {len(result['walls'])}개 · 문 {len(result['doors'])}개")
