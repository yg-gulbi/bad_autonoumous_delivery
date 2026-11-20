from setuptools import find_packages, setup

package_name = 'part3_fleet_manager'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/fleet.yaml']),
        ('share/' + package_name + '/launch', ['launch/fleet_mock.launch.py']),
        ('share/' + package_name + '/mock', ['resource/mock/obstacle_sample.jpg']),
    ],
    install_requires=['setuptools'],
    extras_require={'test': ['pytest']},
    entry_points={'console_scripts': [
        'preflight = part3_fleet_manager.preflight:main',
        'fleet_manager_node = part3_fleet_manager.fleet_manager_node:main',
        'mock_obstacle_classifier = part3_fleet_manager.mock_obstacle_classifier:main',
        'mock_hospital_nav = part3_fleet_manager.mock_hospital_nav:main',
    ]},
    zip_safe=False,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 03 central fleet manager for hospital AMRs.',
    license='UNLICENSED',
)
