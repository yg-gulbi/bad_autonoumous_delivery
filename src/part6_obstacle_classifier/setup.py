from glob import glob
from setuptools import find_packages, setup

setup(
    name='part6_obstacle_classifier',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part6_obstacle_classifier']),
        ('share/part6_obstacle_classifier', ['package.xml', 'USAGE.md']),
        ('share/part6_obstacle_classifier/launch', glob('launch/*.launch.py')),
        ('share/part6_obstacle_classifier/models', glob('models/*.pt')),
    ],
    install_requires=['setuptools'],
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='TurtleBot obstacle classification pipeline.',
    license='UNLICENSED',
    entry_points={'console_scripts': [
        # 순서별 파일(step1..step4)에 매핑된 ROS 2 실행 이름
        'obstacle_classifier = part6_obstacle_classifier.step1_image_event_node:main',
        'rgbd_event_classifier = part6_obstacle_classifier.step2_rgbd_event_node:main',
        'local_image_classifier = part6_obstacle_classifier.step3_local_image_node:main',
        'bag_realtime_obstacle = part6_obstacle_classifier.step4_bag_realtime_node:main',
        # 테스트·디버깅 보조 도구
        'test_turtlebot_viewer = part6_obstacle_classifier.test_turtlebot_viewer:main',
        'test_image_downsample = part6_obstacle_classifier.test_image_downsample:main',
    ]},
)
