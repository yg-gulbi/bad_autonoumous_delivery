from setuptools import find_packages, setup

setup(
    name='part2_qr_verifier',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/part2_qr_verifier']),
        ('share/part2_qr_verifier', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Project contributors',
    maintainer_email='maintainer@example.com',
    description='PART 02 wrist/supply/handover QR verification package (skeleton).',
    license='UNLICENSED',
    entry_points={'console_scripts': []},
)
