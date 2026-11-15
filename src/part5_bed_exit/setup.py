from setuptools import find_packages, setup

setup(
    name='part5_bed_exit',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part5_bed_exit']),
        ('share/part5_bed_exit', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='Replaceable webcam bed-exit judgment with five-frame voting.',
    license='UNLICENSED',
    entry_points={'console_scripts': ['bed_exit = part5_bed_exit.bed_exit_node:main']},
)
