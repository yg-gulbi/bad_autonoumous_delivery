"""Control room for two real AMRs. DDS discovery is configured by the caller."""
from pathlib import Path

from ament_index_python.packages import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    def value(name):
        return LaunchConfiguration(name).perform(context)

    fleet = str(Path(value("config_file")).expanduser())
    db = str(Path(value("db_path")).expanduser())
    manager = {"config_file": fleet, "db_path": db}
    if value("corridor_mode"):
        manager["corridor_mode"] = value("corridor_mode")
    if value("traffic_mode"):
        manager["traffic_mode"] = value("traffic_mode")
    required = value("bed_monitor_require_at_start")
    if required:
        if required.lower() not in ("true", "false"):
            raise ValueError("bed_monitor_require_at_start 값은 true 또는 false여야 합니다.")
        manager["bed_monitor_require_at_start"] = required.lower() == "true"
    ui = {"fleet_yaml": fleet, "db_path": db, "demo": False, "dev_tools": False, "mode_label": "live",
          "ui_config": value("ui_config")}
    if value("map_yaml"):
        ui["map_yaml"] = value("map_yaml")
    return [Node(package="part3_fleet_manager", executable="fleet_manager_node", parameters=[manager], output="screen"),
            Node(package="part1_ui_dashboard", executable="ui_server", parameters=[ui], output="screen")]


def generate_launch_description():
    fleet = get_package_share_path("part3_fleet_manager") / "config/fleet.yaml"
    ui = get_package_share_path("part1_ui_dashboard") / "config/ui.yaml"
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=str(fleet)),
        DeclareLaunchArgument("map_yaml", default_value="", description="PART 04 common occupancy map"),
        DeclareLaunchArgument("db_path", default_value=str(Path.home() / ".bad/bad_real.db")),
        DeclareLaunchArgument("corridor_mode", default_value="", description="Default from fleet.yaml"),
        DeclareLaunchArgument("traffic_mode", default_value="", description="LEGACY or PLAN (empty uses fleet.yaml)"),
        DeclareLaunchArgument("bed_monitor_require_at_start", default_value="", description="Default from fleet.yaml"),
        DeclareLaunchArgument("ui_config", default_value=str(ui)),
        OpaqueFunction(function=start),
    ])
