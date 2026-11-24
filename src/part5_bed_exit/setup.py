"""Part 5를 ROS 2 ``ament_python`` 패키지로 설치하기 위한 패키징 설정.

설치할 Python 패키지와 설정 파일을 지정하고 ``ros2 run part5_bed_exit bed_exit``가
``part5_bed_exit.bed_exit_node:main``을 호출하도록 콘솔 진입점을 등록한다.
"""

from glob import glob
from setuptools import find_packages, setup

setup(
    name='part5_bed_exit',
    version='0.1.0',
    packages=find_packages(exclude=['test', 'test.*', 'research', 'research.*']),
    package_data={'part5_bed_exit': ['methods/*.json']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part5_bed_exit']),
        ('share/part5_bed_exit', ['package.xml']),
        (
            'share/part5_bed_exit/config',
            glob('config/*.default.json')
            + glob('config/*.template.json')
            + glob('config/*.template.yaml')
            + glob('config/*.mp3'),
        ),
        (
            'share/part5_bed_exit/models',
            glob('models/*.pt') + glob('models/*.task'),
        ),
        ('share/part5_bed_exit/launch', glob('launch/*.launch.py')),
        ('share/part5_bed_exit', ['USAGE.md', 'requirements.txt']),
    ],
    install_requires=['setuptools'],
    extras_require={'test': ['pytest']},
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='Replaceable webcam bed-exit judgment with five-frame voting.',
    license='UNLICENSED',
    # [PART05-V5-00 ROS 실행 명령 등록]
    # ``ros2 run part5_bed_exit bed_exit`` → bed_exit_node.main() → BedExitNode.__init__()
    entry_points={'console_scripts': [
        'bed_exit = part5_bed_exit.bed_exit_node:main',
        'label_roi = part5_bed_exit.roi_labeler:main',
        'demo_trigger = part5_bed_exit.demo_trigger_node:main',
    ]},
)
