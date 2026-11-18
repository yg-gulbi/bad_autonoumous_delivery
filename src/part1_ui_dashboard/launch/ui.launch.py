from ament_index_python.packages import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    params = {"ui_config": LaunchConfiguration("ui_config").perform(context)}
    for key in ("map_yaml", "fleet_yaml", "dev_tools", "demo"):
        value = LaunchConfiguration(key).perform(context)
        if value:
            params[key] = (value.lower() == "true") if key in ("demo", "dev_tools") else value
    return [Node(package="part1_ui_dashboard", executable="ui_server", output="screen", parameters=[params])]


def generate_launch_description():
    path = get_package_share_path("part1_ui_dashboard") / "config/ui.yaml"
    return LaunchDescription([DeclareLaunchArgument("ui_config", default_value=str(path)),
                              *[DeclareLaunchArgument(k, default_value="") for k in ("fleet_yaml", "map_yaml", "dev_tools", "demo")],
                              OpaqueFunction(function=start)])
