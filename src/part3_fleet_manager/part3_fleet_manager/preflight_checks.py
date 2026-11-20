"""Local discovery and map checks shared by the read-only field preflight."""
import ast
from ipaddress import ip_address
from math import dist, isclose, isfinite
from pathlib import Path
import socket

import psutil

from .core.robot_protocol import STATES, number


def discovery_ports(setting):
    """Check the control PC's UDP listeners without sending probe packets to robots."""
    endpoints = [s.strip() for s in setting.split(";") if s.strip()]
    if not endpoints:
        return []
    addresses = {a.address.split("%")[0] for items in psutil.net_if_addrs().values() for a in items
                 if a.family in (socket.AF_INET, socket.AF_INET6)}
    listeners = psutil.net_connections(kind="udp")
    checks = []
    for endpoint in endpoints:
        try:
            host, sep, port = endpoint.rpartition(":")
            if not sep or not host or not 0 < int(port) <= 65535:
                raise ValueError
            ips = {item[4][0] for item in socket.getaddrinfo(host.strip("[]"), int(port), type=socket.SOCK_DGRAM)}
        except (OSError, ValueError, OverflowError):
            checks.append((endpoint, False, "주소 또는 포트 확인 필요"))
            continue
        local = {ip for ip in ips if ip in addresses or ip_address(ip).is_loopback}
        if not local:
            checks.append((endpoint, False, "관제 PC의 로컬 주소가 아니어서 서버 바인딩 확인 불가"))
            continue
        owners = []
        for conn in listeners:
            if not conn.laddr or conn.laddr.port != int(port) or conn.raddr:
                continue
            bound = ip_address(conn.laddr.ip.split("%")[0])
            if not any(str(bound) == ip or (bound.is_unspecified and bound.version == ip_address(ip).version)
                       for ip in local):
                continue
            try:
                owner = Path(psutil.Process(conn.pid).exe()).name if conn.pid else "확인 불가"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                owner = "확인 불가"
            owners.append(owner)
        ok = any(name in ("fast-discovery-server", "fastdds", "fastdds-discovery-server") for name in owners)
        message = "UDP 포트 사용 중 (" + ", ".join(sorted(set(owners))) + ")" if owners else "UDP 수신 포트 없음"
        checks.append((endpoint, ok, message))
    return checks


def find_goal_file(config_path):
    package = Path("src/part4_amr_nav/hospital_nav")
    for start in (Path.cwd(), Path(config_path).expanduser().resolve().parent, Path(__file__).resolve().parent):
        for folder in (start, *start.parents):
            for relative in ("hospital_nav/goal_manager.py", "goal_manager.py"):
                path = folder / package / relative
                if path.is_file():
                    return path
    return None


def _goal_point(value, name):
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "GoalPoint"):
        raise ValueError(f"{name} GoalPoint 형식 확인 필요")
    coords = {**dict(zip(("x", "y"), value.args)), **{kw.arg: kw.value for kw in value.keywords}}
    try:
        xy = tuple(ast.literal_eval(coords[k]) for k in ("x", "y") if k in coords)
    except ValueError as exc:
        raise ValueError(f"{name} 좌표 확인 필요") from exc
    if len(xy) != 2 or not all(type(v) in (int, float) and isfinite(v) for v in xy):
        raise ValueError(f"{name} 좌표 확인 필요")
    return xy


def goal_points(path):
    """Read PART 04 goal_manager.py as text, never executing it.

    4J format: SLOTS = {"A": (GoalPoint(..), GoalPoint(..)), ...} and CROSSROAD = GoalPoint(..).
    Earlier format: GOALS = {"A": GoalPoint(..), ...}. Returns {"slots": {zone: [xy, ...]}, "crossroad", "format"}.
    """
    assigned = {}
    for row in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(row, (ast.Assign, ast.AnnAssign)):
            targets = row.targets if isinstance(row, ast.Assign) else [row.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("SLOTS", "GOALS", "CROSSROAD"):
                    assigned.setdefault(target.id, row.value)
    slots, kind = {}, "SLOTS" if isinstance(assigned.get("SLOTS"), ast.Dict) else "GOALS"
    table = assigned.get(kind)
    if table is None:
        raise ValueError("SLOTS 또는 GOALS 정의 없음")
    if not isinstance(table, ast.Dict):
        raise ValueError(f"{kind} 사전 형식 확인 필요")
    for key, value in zip(table.keys, table.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise ValueError(f"{kind} 구역 이름 확인 필요")
        items = value.elts if kind == "SLOTS" and isinstance(value, ast.Tuple) else [value]
        if not 1 <= len(items) <= 2:
            raise ValueError(f"{key.value} 구역 자리 수 확인 필요")
        slots[key.value] = [_goal_point(item, f"{key.value} 구역") for item in items]
    crossroad = assigned.get("CROSSROAD")
    return {"slots": slots, "format": kind,
            "crossroad": None if crossroad is None else _goal_point(crossroad, "CROSSROAD")}


def goal_coordinates(path):
    """Slot 1 of each zone (the 4I preflight output)."""
    return {zone: points[0] for zone, points in goal_points(path)["slots"].items()}


def _far(left, right):
    delta = dist(left, right) if left is not None and right is not None else None
    return delta is None or (delta > .05 and not isclose(delta, .05, rel_tol=0, abs_tol=1e-12)), delta


def compare_zones(zones, goals):
    warnings = []
    for zone in sorted(set(zones) | set(goals)):
        left, right = zones.get(zone), goals.get(zone)
        far, delta = _far(left, right)
        if far:
            suffix = "" if delta is None else f" (거리 {delta:.3f} m)"
            warnings.append(f"{zone} 구역 · 관제 {left} / PART 04 {right}{suffix}".replace("None", "없음"))
    return warnings


def compare_points(config, part4):
    """Both slots of every zone and the crossroad, against fleet.yaml (0.05 m)."""
    warnings = compare_zones(dict(config.zones), {z: points[0] for z, points in part4["slots"].items()})
    for zone in sorted(set(config.slots) | set(part4["slots"])):
        mine = (config.slots.get(zone) or ())[1:2]
        theirs = part4["slots"].get(zone, [])[1:2]
        if not mine and not theirs:
            continue
        left, right = (mine or (None,))[0], (theirs or [None])[0]
        far, delta = _far(left, right)
        if far:
            suffix = "" if delta is None else f" (거리 {delta:.3f} m)"
            warnings.append(f"{zone} 구역 2번 자리 · 관제 {left} / PART 04 {right}{suffix}".replace("None", "없음"))
    if part4["format"] == "SLOTS" or config.corridor.crossroad is not None:
        far, delta = _far(config.corridor.crossroad, part4["crossroad"])
        if far:
            suffix = "" if delta is None else f" (거리 {delta:.3f} m)"
            warnings.append(f"교차로 · 관제 {config.corridor.crossroad} / PART 04 {part4['crossroad']}{suffix}"
                            .replace("None", "없음"))
    return warnings


def slot_line(seen):
    """STATE "slot" decides whether MOVE may carry slot 2 (D-30)."""
    if seen is None:
        return "STATE 없음 — 자리 번호 지원 확인 불가"
    return "있음 (2번 자리 사용)" if seen else "경고 · 예전 PART 04 — 2번 자리 사용 안 함"


def classifier_line(subscribers, mode):
    """PART 06 obstacle_classifier listens to the PART 04 photo topic (report mode)."""
    names = [name for name in subscribers if name not in ("/ui_server", "/fleet_preflight")]
    if names:
        return f"구독자 {len(names)} ({', '.join(names)})"
    if mode != "report":
        return "구독자 0 (inspect 모드: 사용 안 함)"
    return "구독자 0 · 경고 · 장애물 분류기 없음 — 상자 판별 없이 사진만 표시"


def state_rows(report):
    """Required field readiness from a single fresh STATE, independent of the local ROS graph."""
    state = report.get("state")
    battery = report.get("battery")
    pose = report.get("pose")
    docked = report.get("docked")
    percent = number(battery.get("percent")) if isinstance(battery, dict) else None
    xy = None
    if isinstance(pose, dict) and all(number(pose.get(k)) is not None for k in ("x", "y")):
        xy = f"({pose['x']:.3f}, {pose['y']:.3f})"
    battery_text = None
    if (percent is not None and 0 <= percent <= 100 and isinstance(battery.get("status"), str)
            and "voltage" in battery and (battery["voltage"] is None or number(battery["voltage"]) is not None)):
        voltage = "" if battery["voltage"] is None else f", {battery['voltage']:.2f} V"
        battery_text = f"{percent:g}%{voltage}, {battery['status']}"
    return {"hospital_nav/status (STATE)": state if isinstance(state, str) and state in STATES else None,
            "STATE.battery": battery_text, "STATE.pose": xy,
            "STATE.docked": ("도크 위" if docked else "도크 밖") if type(docked) is bool else None}
