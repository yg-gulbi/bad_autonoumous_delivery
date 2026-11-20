"""Spawn one TurtleBot4 in Gazebo + composed Localization (main_maps.yaml) + Nav2 (TB4 defaults).

Uses `turtlebot4_gz_bringup/launch/turtlebot4_spawn.launch.py` and loads TurtleBot4's default
`localization.yaml` and `nav2.yaml` into a single `component_container_mt` per robot to keep
FastDDS localhost participant counts well below the 100-peer default limit.
"""
import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
    TimerAction,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace, SetRemap


def _default_map() -> str:
    return str(Path(__file__).resolve().parents[3] / "main_map" / "main_maps.yaml")


def _nav_actions(context):
    if LaunchConfiguration("launch_nav2").perform(context).lower() != "true":
        return []

    ns = LaunchConfiguration("namespace").perform(context).strip("/")
    ns_slash = f"/{ns}" if ns else ""
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context)
    map_yaml = LaunchConfiguration("map").perform(context)

    pkg_tb4_nav = get_package_share_directory("turtlebot4_navigation")
    pkg_nav2 = get_package_share_directory("nav2_bringup")

    loc_params = os.path.join(pkg_tb4_nav, "config", "localization.yaml")
    nav_params = os.path.join(pkg_tb4_nav, "config", "nav2.yaml")
    loc_launch = os.path.join(pkg_nav2, "launch", "localization_launch.py")
    nav_launch = os.path.join(pkg_tb4_nav, "launch", "navigation_launch.py")

    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]

    nav_group = GroupAction([
        PushRosNamespace(ns),
        SetRemap(f"{ns_slash}/global_costmap/scan", f"{ns_slash}/scan"),
        SetRemap(f"{ns_slash}/local_costmap/scan", f"{ns_slash}/scan"),
        Node(
            name="nav2_container",
            package="rclcpp_components",
            executable="component_container_mt",
            parameters=[{"use_sim_time": use_sim_time.lower() == "true"}],
            remappings=remappings,
            output="screen",
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(loc_launch),
            launch_arguments={
                "namespace": ns_slash,
                "map": map_yaml,
                "use_sim_time": use_sim_time,
                "params_file": loc_params,
                "use_composition": "True",
                "container_name": "nav2_container",
            }.items(),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(nav_launch),
            launch_arguments={
                "namespace": ns_slash,
                "use_sim_time": use_sim_time,
                "params_file": nav_params,
                "use_composition": "True",
                "container_name": "nav2_container",
            }.items(),
        ),
    ])

    return [TimerAction(period=6.0, actions=[nav_group])]


def generate_launch_description():
    pkg_tb4_gz = get_package_share_directory("turtlebot4_gz_bringup")
    spawn_launch = os.path.join(pkg_tb4_gz, "launch", "turtlebot4_spawn.launch.py")

    spawn = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(spawn_launch),
        launch_arguments=[
            ("namespace", LaunchConfiguration("namespace")),
            ("world", LaunchConfiguration("world")),
            ("model", LaunchConfiguration("model")),
            ("use_sim_time", LaunchConfiguration("use_sim_time")),
            ("rviz", "false"),
            ("localization", "false"),
            ("slam", "false"),
            ("nav2", "false"),
            ("x", LaunchConfiguration("x")),
            ("y", LaunchConfiguration("y")),
            ("z", LaunchConfiguration("z")),
            ("yaw", LaunchConfiguration("yaw")),
        ],
    )

    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value="robot3"),
        DeclareLaunchArgument("world", default_value="main_maps"),
        DeclareLaunchArgument("model", default_value="lite", choices=["standard", "lite"]),
        DeclareLaunchArgument("use_sim_time", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("map", default_value=_default_map()),
        DeclareLaunchArgument("launch_nav2", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("x", default_value="-0.05"),
        DeclareLaunchArgument("y", default_value="0.40"),
        DeclareLaunchArgument("z", default_value="0.0"),
        DeclareLaunchArgument("yaw", default_value="0.0"),
        spawn,
        OpaqueFunction(function=_nav_actions),
    ])
