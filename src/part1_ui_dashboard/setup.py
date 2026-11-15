from setuptools import find_packages, setup

setup(
    name='part1_ui_dashboard',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part1_ui_dashboard']),
        ('share/part1_ui_dashboard', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 01 control room UI and rosbridge interface package (skeleton).',
    license='UNLICENSED',
    entry_points={'console_scripts': []},
)
