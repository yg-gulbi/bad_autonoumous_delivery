from setuptools import find_packages, setup

setup(
    name='part4_amr_nav',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part4_amr_nav']),
        ('share/part4_amr_nav', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 04 TurtleBot4 Nav2 autonomous navigation and road-1 arbitration package (skeleton).',
    license='UNLICENSED',
    entry_points={'console_scripts': []},
)
