from setuptools import find_packages, setup

package_name = 'hospital_nav'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='hv-03',
    maintainer_email='tjsdnrla02@naver.com',
    description='TurtleBot4 hospital mission navigation with A-team permission interface',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'keyboard_control = hospital_nav.keyboard_control:main',
            'command_receiver = hospital_nav.command_receiver:main',
        ],
    },
)
