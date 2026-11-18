from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('image_topic', default_value='camera/image_raw'),
        DeclareLaunchArgument('method', default_value=''),
        Node(package='part6_obstacle_classifier', executable='obstacle_classifier',
             output='screen', parameters=[{
                 'image_topic': LaunchConfiguration('image_topic'),
                 'method': LaunchConfiguration('method'),
             }]),
    ])
