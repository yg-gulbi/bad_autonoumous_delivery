"""Part 5 Bed Exit 노드를 YAML 파라미터 파일과 선택적 인자로 실행하는 ROS 2 런치 파일."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ('true', '1', 'yes', 'on'):
        return True
    if lowered in ('false', '0', 'no', 'off'):
        return False
    raise ValueError(f'Invalid boolean launch argument value: {value!r}')


def _build_nodes(context):
    params_file = LaunchConfiguration('params_file').perform(context).strip()
    parameters = []
    if params_file:
        parameters.append(params_file)

    overrides = {}
    for key in ('method', 'method_config', 'video_path', 'output_topic', 'weights', 'device'):
        value = LaunchConfiguration(key).perform(context).strip()
        if value:
            overrides[key] = value

    camera_index = LaunchConfiguration('camera_index').perform(context).strip()
    if camera_index:
        overrides['camera_index'] = int(camera_index)

    frame_rotate_ccw = LaunchConfiguration('frame_rotate_ccw').perform(context).strip()
    if frame_rotate_ccw:
        overrides['frame_rotate_ccw'] = int(frame_rotate_ccw)

    show = LaunchConfiguration('show').perform(context).strip()
    if show:
        overrides['show'] = _parse_bool(show)

    tts_enabled = LaunchConfiguration('tts_enabled').perform(context).strip()
    if tts_enabled:
        overrides['tts_enabled'] = _parse_bool(tts_enabled)

    if overrides:
        parameters.append(overrides)

    return [
        Node(
            package='part5_bed_exit',
            executable='bed_exit',
            name='bed_exit',
            output='screen',
            parameters=parameters,
        )
    ]


def generate_launch_description():
    share_dir = Path(get_package_share_directory('part5_bed_exit'))
    default_params = str(share_dir / 'config' / 'bed_exit.params.template.yaml')
    default_method_config = str(share_dir / 'config' / 'methods.default.json')
    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=default_params,
            description='ROS 2 YAML parameter file for BedExitNode',
        ),
        DeclareLaunchArgument('method', default_value='', description='Optional method override'),
        DeclareLaunchArgument(
            'method_config',
            default_value=default_method_config,
            description='ROI/model JSON config path (defaults to installed methods.default.json)',
        ),
        DeclareLaunchArgument('camera_index', default_value='', description='Optional camera index'),
        DeclareLaunchArgument('video_path', default_value='', description='Optional MP4 input path'),
        DeclareLaunchArgument('output_topic', default_value='', description='Optional output topic'),
        DeclareLaunchArgument('weights', default_value='', description='Optional weights override'),
        DeclareLaunchArgument('device', default_value='', description='Optional device override'),
        DeclareLaunchArgument(
            'frame_rotate_ccw', default_value='', description='Optional CCW rotation (0/90/180/270)'
        ),
        DeclareLaunchArgument('show', default_value='', description='Optional preview bool override'),
        DeclareLaunchArgument(
            'tts_enabled', default_value='', description='Optional TTS enable bool override'
        ),
        OpaqueFunction(function=_build_nodes),
    ])
