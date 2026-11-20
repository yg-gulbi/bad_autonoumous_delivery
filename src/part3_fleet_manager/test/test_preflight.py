"""Field preflight must inspect the local listener and read PART 04 without running it."""
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from part3_fleet_manager import preflight_checks as checks


def test_goal_coordinates_read_without_importing_part4(tmp_path):
    path = tmp_path / "goal_manager.py"
    path.write_text('raise RuntimeError("must not execute")\n'
                    'GOALS: dict = {"A": GoalPoint(-2.2, 0.5), "D": GoalPoint(y=0.4, x=-0.6)}')
    assert checks.goal_coordinates(path) == {"A": (-2.2, 0.5), "D": (-0.6, 0.4)}
    path.write_text('GOALS = {"A": GoalPoint(x=read_x(), y=1)}')
    with pytest.raises(ValueError):
        checks.goal_coordinates(path)


def test_zone_warnings_show_both_values_only_above_five_centimeters():
    warnings = checks.compare_zones({"A": (0.0, 0.0), "B": (1.0, 1.0), "C": (2, 2)},
                                    {"A": (0.03, 0.04), "B": (1.03, 1.05), "D": (3, 3)})
    assert [line[:1] for line in warnings] == ["B", "C", "D"]
    assert "(1.0, 1.0)" in warnings[0] and "(1.03, 1.05)" in warnings[0]
    assert "PART 04 없음" in warnings[1] and "관제 없음" in warnings[2]


def test_discovery_requires_a_fastdds_process_on_the_configured_local_port(monkeypatch):
    def listener(ip, port, pid):
        return SimpleNamespace(laddr=SimpleNamespace(ip=ip, port=port), raddr=(), pid=pid)

    loopback = SimpleNamespace(address="127.0.0.1", family=socket.AF_INET)
    monkeypatch.setattr(checks.psutil, "net_if_addrs", lambda: {"lo": [loopback]})
    monkeypatch.setattr(checks.psutil, "net_connections", lambda kind: [
        listener("0.0.0.0", 11811, 1), listener("127.0.0.1", 11812, 2),
        listener("192.0.2.1", 11813, 1), listener("127.0.0.1", 11814, None)])
    paths = {1: "/usr/bin/fast-discovery-server", 2: "/usr/bin/python3"}
    monkeypatch.setattr(checks.psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: paths[pid]))
    endpoints = ";".join("127.0.0.1:" + str(port) for port in range(11811, 11816))
    rows = checks.discovery_ports(endpoints + ";192.0.2.1:11813;invalid")
    assert [ok for _, ok, _ in rows] == [True, False, False, False, False, False, False]
    assert "python3" in rows[1][2] and "확인 불가" in rows[3][2]
    assert "로컬 주소" in rows[5][2]
    assert checks.discovery_ports("") == []


PART4_PACKAGE = Path(__file__).resolve().parents[2] / "part4_amr_nav/hospital_nav"
PART4_GOALS = next((p for p in (PART4_PACKAGE / "hospital_nav/goal_manager.py", PART4_PACKAGE / "goal_manager.py")
                    if p.is_file()), None)


def test_goal_file_prefers_new_package_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src/part4_amr_nav/hospital_nav"
    nested = package / "hospital_nav"
    nested.mkdir(parents=True)
    (package / "goal_manager.py").touch()
    assert checks.find_goal_file(tmp_path) == package / "goal_manager.py"
    (nested / "goal_manager.py").touch()
    assert checks.find_goal_file(tmp_path) == nested / "goal_manager.py"


@pytest.mark.skipif(PART4_GOALS is None, reason="PART 04 소스가 없는 환경")
def test_62_preflight_reads_the_real_part4_slots_and_crossroad_as_text(cfg):
    points = checks.goal_points(PART4_GOALS)
    assert points["format"] == "SLOTS" and points["crossroad"] == (-1.5, -2.18)
    assert points["slots"]["F"] == [(-1.4, -5.4), (-1.35, -4.7)]
    assert checks.compare_points(cfg, points) == []
    assert checks.goal_coordinates(PART4_GOALS)["B"] == (-2.5, -1.25)


def test_62_preflight_slot_and_crossroad_differences_and_earlier_format(cfg, tmp_path):
    path = tmp_path / "goal_manager.py"
    zones = {z: (round(cfg.slots[z][0][0], 3), round(cfg.slots[z][0][1], 3)) for z in cfg.zones}
    path.write_text("raise SystemExit(1)\nSLOTS = {\n" + "".join(
        f'    "{z}": (GoalPoint({x}, {y}), GoalPoint({cfg.slots[z][1][0] + (0.2 if z == "C" else 0)}, {cfg.slots[z][1][1]})),\n'
        for z, (x, y) in zones.items()) + "}\nCROSSROAD = GoalPoint(-1.5, -2.10)\n")
    warnings = checks.compare_points(cfg, checks.goal_points(path))
    assert [w.split(" · ")[0] for w in warnings] == ["수술실(C) 2번 자리", "교차로"]
    # The earlier PART 04 (one GOALS point per zone, no crossroad) still parses; its slot 2 is "없음".
    path.write_text("GOALS = {" + ", ".join(f'"{z}": GoalPoint({x}, {y})' for z, (x, y) in zones.items()) + "}")
    earlier = checks.goal_points(path)
    assert earlier["format"] == "GOALS" and earlier["crossroad"] is None
    warnings = checks.compare_points(cfg, earlier)
    assert len(warnings) == 7 and all("PART 04 없음" in w for w in warnings)


def test_62_preflight_slot_and_classifier_lines():
    assert checks.slot_line(True) == "있음 (2번 자리 사용)"
    assert checks.slot_line(False) == "경고 · 예전 PART 04 — 2번 자리 사용 안 함"
    assert "확인 불가" in checks.slot_line(None)
    assert checks.classifier_line(["/robot2/obstacle_classifier", "/ui_server"], "report") == \
        "구독자 1 (/robot2/obstacle_classifier)"
    assert checks.classifier_line(["/ui_server"], "report").endswith("장애물 분류기 없음 — 상자 판별 없이 사진만 표시")
    assert "사용 안 함" in checks.classifier_line([], "inspect")
