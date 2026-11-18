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
    ],
    install_requires=['setuptools'],
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='TurtleBot obstacle classification pipeline.',
    license='UNLICENSED',
    entry_points={'console_scripts': [
        'obstacle_classifier = part6_obstacle_classifier.obstacle_classifier_node:main',
        'test_realtime_obstacle = part6_obstacle_classifier.test_realtime_obstacle:main',
    ]},
)
