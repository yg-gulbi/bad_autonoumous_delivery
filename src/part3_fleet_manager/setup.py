from setuptools import find_packages, setup

setup(
    name='part3_fleet_manager',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part3_fleet_manager']),
        ('share/part3_fleet_manager', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 03 central fleet dispatch, road-1 occupancy, and safety pause core (skeleton).',
    license='UNLICENSED',
    entry_points={'console_scripts': []},
)
