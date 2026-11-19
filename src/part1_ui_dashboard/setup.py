from glob import glob
from setuptools import find_packages, setup

setup(
    name='part1_ui_dashboard',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    package_data={'part1_ui_dashboard': ['static/*', 'static/pages/*.js', 'static/fonts/*']},
    data_files=[
        ('share/part1_ui_dashboard/config', ['config/ui.yaml', 'config/floor_plan.yaml']),
        ('share/part1_ui_dashboard/launch', glob('launch/*.launch.py')),
        ('share/ament_index/resource_index/packages', ['resource/part1_ui_dashboard']),
        ('share/part1_ui_dashboard', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=False,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 01 control room UI and fleet operations dashboard.',
    license='UNLICENSED',
    entry_points={'console_scripts': [
        'ui_server = part1_ui_dashboard.ui_server_node:main',
        'measure_floor_plan = part1_ui_dashboard.ui_api:measure_main',
    ]},
)
