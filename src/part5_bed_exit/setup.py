from setuptools import find_packages, setup
from glob import glob

setup(
    name='part5_bed_exit',
    version='0.1.0',
    packages=find_packages(exclude=['test', 'test.*', 'research', 'research.*']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part5_bed_exit']),
        ('share/part5_bed_exit', ['package.xml']),
        ('share/part5_bed_exit/examples', glob('examples/*.template.json')),
        ('share/part5_bed_exit', ['USAGE.md', 'requirements.txt']),
    ],
    install_requires=['setuptools'],
    extras_require={'test': ['pytest']},
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='Replaceable webcam bed-exit judgment with five-frame voting.',
    license='UNLICENSED',
    entry_points={'console_scripts': ['bed_exit = part5_bed_exit.bed_exit_node:main']},
)
