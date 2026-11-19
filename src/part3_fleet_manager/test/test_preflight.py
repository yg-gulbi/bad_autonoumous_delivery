"""Field preflight must inspect the local listener and read PART 04 without running it."""
import socket
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
