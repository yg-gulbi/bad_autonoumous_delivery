#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
WORKSPACE=$(cd -- "$PACKAGE_ROOT/../.." && pwd)
RESULT_BASE="$PACKAGE_ROOT/results/80805b4_v1_3"
RUN_ID=${PART5_RUN_ID:-$(date +%Y%m%d_%H%M%S)}
RUN_ROOT="$RESULT_BASE/runs/$RUN_ID"
PROFILE=${1:-}

if [[ -n "${PART5_PYTHON:-}" ]]; then
  PYTHON_BIN=$PART5_PYTHON
elif [[ -x "$PACKAGE_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$PACKAGE_ROOT/.venv/bin/python"
else
  PYTHON_BIN=python3
fi

if [[ ! -x "$PYTHON_BIN" ]] && ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  exit 2
fi
if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
  echo 'ROS 2 Jazzy setup not found: /opt/ros/jazzy/setup.bash' >&2
  exit 2
fi

# ROS/colcon setup scripts read optional variables before defining them and are
# therefore not compatible with `set -u`. Limit nounset relaxation to sourcing.
set +u
source /opt/ros/jazzy/setup.bash
if [[ -f "$WORKSPACE/install/setup.bash" ]]; then
  source "$WORKSPACE/install/setup.bash"
fi
set -u
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=${PART5_ROS_DOMAIN_ID:-103}
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export PYTHONPATH="$PACKAGE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOG_DIR="$RUN_ROOT/ros_logs"

mkdir -p "$ROS_LOG_DIR"

run_unit() {
  mkdir -p "$RUN_ROOT/unit_tests"
  "$PYTHON_BIN" -m pytest "$SCRIPT_DIR/unit" -q \
    --junitxml="$RUN_ROOT/unit_tests/pytest.xml"
}

run_integration() {
  mkdir -p "$RUN_ROOT/integration_tests"
  PART5_RUN_V3_MP4_INTEGRATION=1 "$PYTHON_BIN" -m pytest \
    "$SCRIPT_DIR/integration/test_v3_dynamic_pipeline.py" \
    "$SCRIPT_DIR/integration/test_v3_mp4_replay_resilience.py" \
    "$SCRIPT_DIR/integration/test_v3_mp4_runtime_loader.py" \
    -q --junitxml="$RUN_ROOT/integration_tests/pytest.xml"
}

run_benchmark() {
  local benchmark_root="$RUN_ROOT/integration_benchmark"
  local video="$PACKAGE_ROOT/research/data/videos/test_input_sequence1.mp4"
  local config="$PACKAGE_ROOT/research/configs/test_input_sequence1_methods_v1.json"
  local timeline="$PACKAGE_ROOT/research/configs/test_input_sequence1_timeline_v1.json"
  local method

  for method in detection segmentation yolo_pose mediapipe_pose; do
    "$PYTHON_BIN" -m research.integration_benchmark.benchmark \
      --video "$video" \
      --config "$config" \
      --timeline "$timeline" \
      --output-dir "$benchmark_root/individual/$method" \
      --mode individual \
      --method "$method"
  done

  "$PYTHON_BIN" -m research.integration_benchmark.benchmark \
    --video "$video" \
    --config "$config" \
    --timeline "$timeline" \
    --output-dir "$benchmark_root/all_modules" \
    --mode all

  PART5_VERIFY_V3_BENCHMARK_OUTPUTS=1 \
  PART5_V3_BENCHMARK_RESULT_ROOT="$benchmark_root" \
    "$PYTHON_BIN" -m pytest "$SCRIPT_DIR/integration_benchmark" -q \
      --junitxml="$benchmark_root/pytest.xml"
}

case "$PROFILE" in
  unit)
    run_unit
    ;;
  integration)
    run_integration
    ;;
  benchmark)
    run_benchmark
    ;;
  *)
    echo "Usage: $0 {unit|integration|benchmark}" >&2
    exit 2
    ;;
esac

printf 'Part 5 v1.3 validation complete.\n'
printf 'Python: %s\n' "$PYTHON_BIN"
printf 'Profile: %s\n' "$PROFILE"
printf 'Results: %s\n' "$RUN_ROOT"
