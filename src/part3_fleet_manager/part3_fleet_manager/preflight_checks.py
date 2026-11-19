"""Local discovery and map checks shared by the read-only field preflight."""
import ast
from ipaddress import ip_address
from math import dist, isclose, isfinite
from pathlib import Path
import socket

import psutil


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
    relative = Path("src/part4_amr_nav/hospital_nav/goal_manager.py")
    for start in (Path.cwd(), Path(config_path).expanduser().resolve().parent, Path(__file__).resolve().parent):
        for folder in (start, *start.parents):
            if (folder / relative).is_file():
                return folder / relative
    return None


def goal_coordinates(path):
    """Read literal GoalPoint coordinates without executing PART 04 Python code."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for row in tree.body:
        if isinstance(row, (ast.Assign, ast.AnnAssign)):
            targets = row.targets if isinstance(row, ast.Assign) else [row.target]
            if any(isinstance(t, ast.Name) and t.id == "GOALS" for t in targets):
                break
    else:
        raise ValueError("GOALS 정의 없음")
    if not isinstance(row.value, ast.Dict):
        raise ValueError("GOALS 사전 형식 확인 필요")
    goals = {}
    for key, value in zip(row.value.keys, row.value.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name) and value.func.id == "GoalPoint"):
            raise ValueError("GOALS 구역 또는 GoalPoint 형식 확인 필요")
        coords = {**dict(zip(("x", "y"), value.args)), **{kw.arg: kw.value for kw in value.keywords}}
        xy = tuple(ast.literal_eval(coords[k]) for k in ("x", "y") if k in coords)
        if len(xy) != 2 or not all(type(v) in (int, float) and isfinite(v) for v in xy):
            raise ValueError(f"{key.value} 구역 좌표 확인 필요")
        goals[key.value] = xy
    return goals


def compare_zones(zones, goals):
    warnings = []
    for zone in sorted(set(zones) | set(goals)):
        left, right = zones.get(zone), goals.get(zone)
        delta = dist(left, right) if left is not None and right is not None else None
        if delta is None or (delta > .05 and not isclose(delta, .05, rel_tol=0, abs_tol=1e-12)):
            suffix = "" if delta is None else f" (거리 {delta:.3f} m)"
            warnings.append(f"{zone} 구역 · 관제 {left} / PART 04 {right}{suffix}".replace("None", "없음"))
    return warnings
