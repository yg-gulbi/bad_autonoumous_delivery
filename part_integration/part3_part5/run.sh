#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORKSPACE=$(cd -- "$SCRIPT_DIR/../.." && pwd)
PYTHON_BIN=${PART_INTEGRATION_PYTHON:-/home/mu-02/venvs/rokey_venv/bin/python3}
RESULT_VERSION=80805b4_v1_3

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  echo 'Set PART_INTEGRATION_PYTHON to an executable Python 3 path.' >&2
  exit 2
fi
if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
  echo 'ROS 2 Jazzy setup not found: /opt/ros/jazzy/setup.bash' >&2
  exit 2
fi
if [[ ! -f "$WORKSPACE/install/setup.bash" ]]; then
  echo 'Workspace is not built. Run colcon build first.' >&2
  exit 2
fi

# ROS/colcon setup scripts read optional variables before defining them.
set +u
source /opt/ros/jazzy/setup.bash
source "$WORKSPACE/install/setup.bash"
set -u
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT

exec "$PYTHON_BIN" -u \
  "$SCRIPT_DIR/run_part3_part5_ros_experiment.py" \
  --output-root "$SCRIPT_DIR/results/$RESULT_VERSION" \
  "$@"
