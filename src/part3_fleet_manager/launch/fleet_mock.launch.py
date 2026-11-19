from ament_index_python.packages import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from part3_fleet_manager.core.config import load_config


def launch_nodes(context):
    package = "part3_fleet_manager"
    path = LaunchConfiguration("config_file").perform(context)
    config = load_config(path)
    manager = Node(package=package, executable="fleet_manager_node", output="screen", parameters=[{
        "config_file": path, "bed_monitor_require_at_start": False,
        "db_path": ParameterValue(LaunchConfiguration("db_path"), value_type=str),
        "assignment_mode": LaunchConfiguration("assignment_mode").perform(context) or config.assignment.mode}])
    mocks = []
    for robot, unit in config.robots.items():
        params = {"config_file": path}
        r1 = LaunchConfiguration(robot + "_r1", default="")
        speed = LaunchConfiguration("mock_speed")
        battery = LaunchConfiguration(robot + "_battery", default="")
        if battery.perform(context):
            params["battery_pct"] = ParameterValue(battery, value_type=float)
        if r1.perform(context):
            params["supports_request_id"] = ParameterValue(r1, value_type=bool)
            params["report_rejections"] = ParameterValue(r1, value_type=bool)
        if speed.perform(context):
            params["speed_mps"] = ParameterValue(speed, value_type=float)
        mocks.append(Node(package=package, executable="mock_hospital_nav", namespace=unit.ns, output="screen",
                          parameters=[params]))
        if config.obstacle.enabled:
            mocks.append(Node(package=package, executable="mock_obstacle_classifier", namespace=unit.ns,
                              output="screen", parameters=[{"config_file": path}]))
    return [manager, *mocks]


def generate_launch_description():
    default = get_package_share_path("part3_fleet_manager") / "config" / "fleet.yaml"
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=str(default)),
        DeclareLaunchArgument("db_path", default_value="", description="Override the configured SQLite file"),
        DeclareLaunchArgument("mock_speed", default_value="", description="Override mock.speed_mps for both robots"),
        DeclareLaunchArgument("robot_a_r1", default_value="", description="Override R1 support on robot_a"),
        DeclareLaunchArgument("robot_b_r1", default_value="", description="Override R1 support on robot_b"),
        DeclareLaunchArgument("assignment_mode", default_value="", description="IDLE_BEST_BATTERY or ROLE"),
        DeclareLaunchArgument("robot_a_battery", default_value="", description="Robot A initial battery (%)"),
        DeclareLaunchArgument("robot_b_battery", default_value="", description="Robot B initial battery (%)"),
        OpaqueFunction(function=launch_nodes),
    ])
