import ast
from pathlib import Path

import pytest
import yaml

from conftest import CONFIG_PATH
from part3_fleet_manager.core.config import ConfigError, config_from_dict, load_config


def test_default_yaml_loads(cfg):
    assert [(r.label, r.mark) for r in cfg.robots.values()] == [("침상형 이송 로봇", "A"), ("약품 운송 로봇", "B")]
    # D-29 (11/19): robot3 is the bed transport robot, robot2 carries medicine and owns the measured dock.
    assert cfg.robots["robot_a"].ns == "robot3" and cfg.robots["robot_a"].dock is None
    assert cfg.robots["robot_b"].ns == "robot2"
    assert (cfg.robots["robot_b"].dock.x, cfg.robots["robot_b"].dock.y, cfg.robots["robot_b"].dock.yaw_deg) == (-0.330, -5.110, -3.0)
    assert {robot.start_zone for robot in cfg.robots.values()} == {"D", "F"}
    assert cfg.type_to_robot == {"PATIENT_TRANSPORT": "robot_a", "MED_ROUND": "robot_b", "CALL": "robot_b"}
    assert (cfg.assignment.mode, cfg.assignment.min_battery_pct) == ("ROLE", 20)
    assert cfg.mission.return_home and cfg.mission.return_interruptible
    assert cfg.robot_link.command_topic.format(ns=cfg.robots["robot_a"].ns) == (
        "/robot3/hospital_nav/command"
    )
    assert cfg.robot_link.request_id == "auto" and cfg.robot_link.start_timeout_s == 120
    assert not cfg.safety.use_hazard_topic and all(r.return_to == "ZONE" for r in cfg.robots.values())
    assert not cfg.mock.report_rejections and cfg.mock.localizing_s == 3
    assert set(cfg.zones) == set("ABCDEF")
    assert cfg.corridor.mouths["ROOM_E"] == (-1.08, -2.25)
    assert cfg.corridor.yield_enabled
    assert cfg.safety.bump_neighbor_m == 1.0 and cfg.publish.finished_mission_limit == 20
    assert (cfg.corridor.yield_trigger_m, cfg.corridor.yield_release_m, cfg.corridor.yield_timeout_s) == (1.2, 0.5, 10)
    assert cfg.resources.clear_margin_m == 0.20
    assert (cfg.corridor.follow_gap_m, cfg.corridor.follow_resume_m) == (1.0, 1.4)
    assert (cfg.safety.proximity_stop_m, cfg.safety.proximity_resume_m) == (0.6, 0.9)
    assert cfg.resources.zone_occupied_m == 0.6
    assert cfg.resources.aging_s == 0
    assert cfg.deadlock.yield_zones == {"ROOM_B": "D", "ROOM_E": "F"}
    assert cfg.safety.bed_exit_pause_targets == ("robot_a",)
    assert cfg.safety.bed_monitor.exit_clear_hold_s == 1.0
    assert cfg.mission.confirm_at_stops and not cfg.mission.return_to_dock
    assert (cfg.mock.speed_mps, cfg.mock.stall_radius_m, cfg.mock.supports_request_id) == (0.2, 0.05, False)
    assert (cfg.publish.fleet_state_hz, cfg.publish.tick_hz) == (5, 10)
    assert cfg.db_path == str(Path("~/.bad/bad.db").expanduser())
    # D-30: two slots per zone (PART 04 goal_manager.SLOTS), B·E doors and the shared crossroad.
    assert cfg.slots["F"] == ((-1.4, -5.4), (-1.35, -4.7)) and cfg.slot_point("F", 2) == (-1.35, -4.7)
    assert all(cfg.slot_count(z) == 2 for z in cfg.zones) and cfg.zones["B"] == (-2.5, -1.25)
    assert cfg.corridor.crossroad == (-1.5, -2.18) and cfg.corridor.crossroad_clear_m == 0.6
    assert cfg.corridor.room_mode == "DOOR" and cfg.corridor.doors
    # D-31: obstacle report mode, medical box label. 4K: no automatic retry when PART 06 sees nothing.
    assert (cfg.obstacle.mode, cfg.obstacle.timeout_s, cfg.obstacle.retry_if_clear) == ("report", 6.0, False)
    # D-32: PAUSE resend every 1.5 s, the card after more than 3 resends.
    assert (cfg.robot_link.control_retry_s, cfg.robot_link.control_retries) == (1.5, 3)
    assert cfg.obstacle.label_ko["medical_box"] == "의료용 상자" and cfg.obstacle.none_text == "의료용 상자는 보이지 않음"
    assert cfg.obstacle.image_topic.format(ns="robot2") == "/robot2/hospital_nav/obstacle_image"
    assert cfg.mock.abort_after_s == 5.0 and cfg.safety.slot_stop_m == 0.45


@pytest.mark.parametrize(("path", "value"), [
    ("robots.robot_b.ns", "robot3"),
    ("robots.robot_b.mark", "A"),
    ("robots.robot_a.mark", "AB"),
    ("robots.robot_a.start_zone", "Z"),
    ("robots.robot_a.return_to", "HOME"),
    ("robots.robot_b.dock.yaw_deg", float("nan")),
    ("robot_link.start_timeout_s", 0),
    ("safety.use_hazard_topic", "false"),
    ("mock.localizing_s", -1),
    ("mock.report_rejections", "false"),
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
    ("zones.B.slot2", {"x": -1.5, "y": -2.18}),
    ("corridor.crossroad", {"x": -0.4, "y": -1.4}),
    ("corridor.crossroad_clear_m", 0),
    ("corridor.room_mode", "HALL"),
    ("obstacle.mode", "auto"),
    ("obstacle.retry_if_clear", "yes"),
    ("obstacle.report_wait_s", 0),
    ("obstacle.image_topic", "/robot2/hospital_nav/obstacle_image"),
    ("obstacle.none_text", ""),
    ("safety.slot_stop_m", 0),
    ("safety.bed_monitor.exit_clear_hold_s", -0.1),
    ("zones.F.slot2_blocks_exit", "false"),
    ("mock.abort_after_s", 0),
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
    ("safety.bump_neighbor_m", -1),
    ("corridor.follow_gap_m", 0),
    ("corridor.follow_resume_m", 1.0),
    ("safety.proximity_stop_m", -0.1),
    ("safety.proximity_resume_m", 0.5),
    ("resources.zone_occupied_m", float("nan")),
    ("publish.finished_mission_limit", -1),
    ("publish.finished_mission_limit", True),
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


def test_fleet_file_before_4j_keeps_one_slot_rooms_and_inspection():
    """An older fleet.yaml (no slot2, crossroad or obstacle.mode) keeps the 4I behaviour."""
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for zone in data["zones"].values():
        del zone["slot2"]
        zone.pop("slot2_blocks_exit", None)
    for key in ("crossroad", "crossroad_clear_m", "room_mode"):
        del data["corridor"][key]
    del data["obstacle"]["mode"]
    config = config_from_dict(data)
    assert config.corridor.room_mode == "ROOM" and not config.corridor.doors
    assert config.slot_count("C") == 1 and config.slot_point("C", 2) == config.zones["C"]
    assert not config.exit_blocked_zones
    assert config.obstacle.mode == "inspect"
    data["corridor"]["room_mode"] = "DOOR"
    with pytest.raises(ConfigError, match="corridor.room_mode"):
        config_from_dict(data)


def test_exit_policy_defaults_and_local_layout_override():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for zone in data["zones"].values():
        del zone["slot2_blocks_exit"]
    assert config_from_dict(data).exit_blocked_zones == {"A", "F"}
    data["zones"]["F"]["slot2_blocks_exit"] = False
    data["zones"]["C"]["slot2_blocks_exit"] = True
    assert config_from_dict(data).exit_blocked_zones == {"A", "C"}
    del data["zones"]["C"]["slot2"]
    with pytest.raises(ConfigError, match="zones.C.slot2_blocks_exit"):
        config_from_dict(data)


def test_previous_fleet_file_without_policy_uses_directional_defaults():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in ("mode", "yield_enabled", "yield_trigger_m", "yield_release_m", "yield_timeout_s"):
        del data["corridor"][key]
    corridor = config_from_dict(data).corridor
    assert corridor.mode == "DIRECTIONAL" and corridor.yield_enabled
    assert (corridor.yield_trigger_m, corridor.yield_release_m, corridor.yield_timeout_s) == (1.2, 0.5, 10.0)
