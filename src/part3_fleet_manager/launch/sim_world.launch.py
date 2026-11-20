"""Launch Gazebo Harmonic (headless server by default) with main_maps.sdf and /clock bridge."""
import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _ws_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _setup(context):
    ws = _ws_root()
    sim_dir = ws / "src" / "part3_fleet_manager" / "sim"
    world = LaunchConfiguration("world").perform(context)
    headless = LaunchConfiguration("headless").perform(context).lower() == "true"

    pkg_tb4_gz = get_package_share_directory("turtlebot4_gz_bringup")
    pkg_tb4_gui = get_package_share_directory("turtlebot4_gz_gui_plugins")
    pkg_tb4_desc = get_package_share_directory("turtlebot4_description")
    pkg_c3_desc = get_package_share_directory("irobot_create_description")
    pkg_c3_gz = get_package_share_directory("irobot_create_gz_bringup")
    pkg_c3_plugins = get_package_share_directory("irobot_create_gz_plugins")
    pkg_ros_gz_sim = get_package_share_directory("ros_gz_sim")

    gz_resource = ":".join(p for p in [
        str(sim_dir),
        os.path.join(pkg_tb4_gz, "worlds"),
        os.path.join(pkg_c3_gz, "worlds"),
        str(Path(pkg_tb4_desc).parent.resolve()),
        str(Path(pkg_c3_desc).parent.resolve()),
        os.environ.get("GZ_SIM_RESOURCE_PATH", ""),
    ] if p)
    gz_gui_plugins = ":".join([
        os.path.join(pkg_tb4_gui, "lib"),
        os.path.join(pkg_c3_plugins, "lib"),
    ])

    gz_flags = f"{world}.sdf -r -s -v 2" if headless else f"{world}.sdf -r -v 2"

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(pkg_ros_gz_sim, "launch", "gz_sim.launch.py")),
        launch_arguments=[("gz_args", gz_flags)],
    )

    clock_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="clock_bridge",
        output="screen",
        arguments=["/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
    )

    return [
        SetEnvironmentVariable(name="GZ_SIM_RESOURCE_PATH", value=gz_resource),
        SetEnvironmentVariable(name="GZ_GUI_PLUGIN_PATH", value=gz_gui_plugins),
        SetEnvironmentVariable(name="__NV_PRIME_RENDER_OFFLOAD", value="1"),
        SetEnvironmentVariable(name="__GLX_VENDOR_LIBRARY_NAME", value="nvidia"),
        gazebo,
        clock_bridge,
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("world", default_value="main_maps", description="World name (without .sdf)"),
        DeclareLaunchArgument("headless", default_value="true", choices=["true", "false"]),
        OpaqueFunction(function=_setup),
    ])
