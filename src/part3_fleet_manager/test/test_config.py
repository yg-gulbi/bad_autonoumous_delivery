import ast
from pathlib import Path

import pytest
import yaml

from conftest import CONFIG_PATH
from part3_fleet_manager.core.config import ConfigError, config_from_dict, load_config


def test_default_yaml_loads(cfg):
    assert cfg.robots["robot_a"].ns == "robot2"
    assert cfg.robots["robot_b"].ns == "robot3"
    assert {robot.start_zone for robot in cfg.robots.values()} == {"D", "F"}
    assert cfg.type_to_robot == {"PATIENT_TRANSPORT": "robot_a", "MED_ROUND": "robot_b", "CALL": "robot_b"}
    assert (cfg.assignment.mode, cfg.assignment.min_battery_pct) == ("IDLE_BEST_BATTERY", 20)
    assert cfg.mission.return_home and cfg.mission.return_interruptible
    assert cfg.robot_link.command_topic.format(ns=cfg.robots["robot_a"].ns) == (
        "/robot2/hospital_nav/command"
    )
    assert cfg.robot_link.request_id == "auto"
    assert set(cfg.zones) == set("ABCDEF")
    assert cfg.corridor.mouths["ROOM_E"] == (-1.08, -2.25)
    assert cfg.corridor.mode == "SHARED_YIELD" and cfg.corridor.yield_enabled
    assert (cfg.corridor.yield_trigger_m, cfg.corridor.yield_release_m, cfg.corridor.yield_timeout_s) == (1.2, 0.5, 10)
    assert cfg.resources.clear_margin_m == 0.20
    assert cfg.resources.aging_s == 0
    assert cfg.deadlock.yield_zones == {"ROOM_B": "D", "ROOM_E": "F"}
    assert cfg.safety.bed_exit_pause_targets == ("robot_a",)
    assert cfg.mission.confirm_at_stops and not cfg.mission.return_to_dock
    assert (cfg.mock.speed_mps, cfg.mock.stall_radius_m, cfg.mock.supports_request_id) == (0.2, 0.05, True)
    assert (cfg.publish.fleet_state_hz, cfg.publish.tick_hz) == (5, 10)
    assert cfg.db_path == str(Path("~/.bad/bad.db").expanduser())


@pytest.mark.parametrize(("path", "value"), [
    ("robots.robot_b.ns", "robot2"),
    ("robots.robot_a.start_zone", "Z"),
    ("type_to_robot.MED_ROUND", "missing"),
    ("assignment.mode", "ROUND_ROBIN"),
    ("assignment.min_battery_pct", 101),
    ("mission.return_home", "false"),
    ("mission.return_interruptible", "true"),
    ("robot_link.mode", "OTHER"),
    ("robot_link.request_id", "sometimes"),
    ("robot_link.command_topic", "/robot2/hospital_nav/command"),
    ("robot_link.status_topic", "/{ns}/{ns}"),
    ("robot_link.link_timeout_s", 0),
    ("zones.A", {"x": -1.6, "y": -2}),
    ("zones.E", {"x": float("nan"), "y": 0}),
    ("zones.E.x", 10 ** 350),
    ("corridor.mode", "SHARE"),
    ("corridor.yield_enabled", "false"),
    ("corridor.yield_trigger_m", 0),
    ("corridor.yield_release_m", -0.5),
    ("corridor.yield_timeout_s", float("inf")),
    ("corridor.include_boundary", "true"),
    ("corridor.polygon", [[0, 0], [2, 2], [0, 2], [2, 0]]),
    ("resources.clear_margin_m", -0.2),
    ("resources.aging_s", True),
    ("deadlock.yield_zones.ROOM_E", "E"),
    ("stall.long_s", 5),
    ("safety.bed_exit_pause_targets", ["robot2"]),
    ("mission.return_to_dock", "false"),
    ("mock.speed_mps", 0),
    ("mock.stall_radius_m", -1),
    ("mock.supports_request_id", "false"),
    ("publish.tick_hz", float("inf")),
    ("db.path", ""),
])
def test_invalid_setting_identifies_field(path, value):
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    node = data
    *parents, key = path.split(".")
    for parent in parents:
        node = node[parent]
    node[key] = value
    with pytest.raises(ConfigError, match=path.replace(".", r"\.")):
        config_from_dict(data)


def test_malformed_yaml_is_a_config_error(tmp_path):
    invalid = tmp_path / "fleet.yaml"
    invalid.write_text("robots: [")
    with pytest.raises(ConfigError, match="fleet.yaml"):
        load_config(invalid)


def test_brief_yaml_without_boundary_setting_is_valid():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    del data["corridor"]["include_boundary"]
    assert config_from_dict(data).corridor.include_boundary is False


def test_core_has_no_ros_imports():
    package_path = CONFIG_PATH.parents[1] / "part3_fleet_manager" / "core"
    ros_packages = {"rclpy", "ament_index_python", "geometry_msgs", "sensor_msgs", "std_msgs",
                    "std_srvs", "irobot_create_msgs", "part4_amr_nav", "hospital_nav"}
    for source in package_path.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = [node.module]
            else:
                continue
            assert not ros_packages.intersection(name.split(".")[0] for name in imports), source


def test_previous_fleet_file_inherits_shared_yield_defaults(cfg):
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in ("mode", "yield_enabled", "yield_trigger_m", "yield_release_m", "yield_timeout_s"):
        del data["corridor"][key]
    assert config_from_dict(data).corridor == cfg.corridor
