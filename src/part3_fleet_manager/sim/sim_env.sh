#!/usr/bin/env bash
# Isolated ROS 2 environment for Gazebo simulation via local FastDDS discovery server (127.0.0.1:11888).
# Never mixes with real robots (domain 2 / classroom discovery server).
source ~/venvs/rokey_venv/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/turtlebot4_ws/install/setup.bash
source /home/kimjunseong852/bad_ws/install/setup.bash
unset ROS_AUTOMATIC_DISCOVERY_RANGE ROS_LOCALHOST_ONLY ROS_SUPER_CLIENT
export ROS_DISCOVERY_SERVER=127.0.0.1:11888
export ROS_DOMAIN_ID=97
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
if [ "${1:-}" = "--super" ] || [ "${SIM_SUPER:-0}" = "1" ]; then
  export ROS_SUPER_CLIENT=True
fi
