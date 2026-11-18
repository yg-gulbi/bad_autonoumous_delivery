from ament_index_python.packages import get_package_share_path
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    fleet = get_package_share_path("part3_fleet_manager")
    ui = get_package_share_path("part1_ui_dashboard")
    defaults = {"fleet_yaml": str(fleet / "config/fleet.yaml"), "ui_config": str(ui / "config/ui.yaml"),
                "map_yaml": "", "db_path": "", "mock_speed": "", "assignment_mode": "",
                "robot_a_r1": "", "robot_b_r1": "", "robot_a_battery": "", "robot_b_battery": ""}
    args = {k: LaunchConfiguration(k) for k in defaults}
    fleet_args = {k: args[k] for k in defaults if k not in ("fleet_yaml", "ui_config", "map_yaml")}
    fleet_args["config_file"] = args["fleet_yaml"]
    ui_args = {k: args[k] for k in ("fleet_yaml", "ui_config", "map_yaml", "db_path")}
    ui_args["dev_tools"] = ui_args["demo"] = "true"
    return LaunchDescription([
        *[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(fleet / "launch/fleet_mock.launch.py")),
                                 launch_arguments=fleet_args.items()),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(ui / "launch/ui.launch.py")),
                                 launch_arguments=ui_args.items()),
    ])
