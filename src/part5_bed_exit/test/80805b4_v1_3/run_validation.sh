#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
WORKSPACE=$(cd -- "$PACKAGE_ROOT/../.." && pwd)
RESULT_BASE="$PACKAGE_ROOT/results/80805b4_v1_3"
PROFILE=${1:-}
RUN_ID=${PART5_RUN_ID:-$(date +%Y%m%d_%H%M%S)}

case "$PROFILE" in
  unit|integration|benchmark) ;;
  *)
    echo "Usage: $0 {unit|integration|benchmark}" >&2
    exit 2
    ;;
esac

RUN_ROOT="$RESULT_BASE/${RUN_ID}_${PROFILE}"
if [[ -e "$RUN_ROOT" ]]; then
  echo "Result directory already exists: $RUN_ROOT" >&2
  exit 2
fi

case "$PROFILE" in
  unit) PLANNED_SUITES=1 ;;
  integration) PLANNED_SUITES=3 ;;
  benchmark) PLANNED_SUITES=6 ;;
esac
SUITES_STARTED=0
SUITES_FINISHED=0
SUITES_PASSED=0
SUITES_FAILED=0

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

# ROS/colcon setup scripts read optional variables before defining them.
set +u
source /opt/ros/jazzy/setup.bash
if [[ -f "$WORKSPACE/install/setup.bash" ]]; then
  source "$WORKSPACE/install/setup.bash"
fi
set -u
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=${PART5_ROS_DOMAIN_ID:-103}
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export PYTHONPATH="$SCRIPT_DIR:$PACKAGE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOG_DIR="$RUN_ROOT/ros_logs"

mkdir -p "$ROS_LOG_DIR"
exec > >(tee "$RUN_ROOT/execution.log") 2>&1

run_step() {
  local index=$1
  local total=$2
  local name=$3
  local expected=$4
  shift 4
  local started=$SECONDS
  local status

  SUITES_STARTED=$((SUITES_STARTED + 1))
  printf '\n[SUITE %s/%s START] %s\n' "$index" "$total" "$name"
  printf '[EXPECTED] %s\n' "$expected"
  if "$@"; then
    status=PASS
  else
    status=FAIL
  fi
  SUITES_FINISHED=$((SUITES_FINISHED + 1))
  if [[ "$status" == PASS ]]; then
    SUITES_PASSED=$((SUITES_PASSED + 1))
  else
    SUITES_FAILED=$((SUITES_FAILED + 1))
  fi
  printf '[SUITE %s/%s END] %s duration_s=%s\n' \
    "$index" "$total" "$status" "$((SECONDS - started))"
  [[ "$status" == PASS ]]
}

pytest_suite() {
  local junit=$1
  shift
  "$PYTHON_BIN" -m pytest "$@" -q -s -p validation_reporter --junitxml="$junit"
}

run_unit() {
  mkdir -p "$RUN_ROOT/unit_tests"
  run_step 1 1 "Part 5 unit tests" \
    "모든 공개 동작·예외 안전 정책 테스트가 통과한다" \
    pytest_suite "$RUN_ROOT/unit_tests/pytest.xml" "$SCRIPT_DIR/unit"
}

run_integration() {
  local root="$RUN_ROOT/integration_tests"
  mkdir -p "$root"

  run_step 1 3 "Dynamic loader and five-frame vote" \
    "외부 메서드를 로딩하고 최근 5프레임 중 True 3회 규칙에 도달한다" \
    pytest_suite "$root/dynamic_pipeline.xml" \
    "$SCRIPT_DIR/integration/test_v3_dynamic_pipeline.py" || return 1

  run_step 2 3 "MP4 replay exception resilience" \
    "읽기·발행·프리뷰 예외를 격리하고 투표 이력을 초기화한다" \
    pytest_suite "$root/replay_resilience.xml" \
    "$SCRIPT_DIR/integration/test_v3_mp4_replay_resilience.py" || return 1

  export PART5_RUN_V3_MP4_INTEGRATION=1
  export PART5_INTEGRATION_METRICS_PATH="$root/mp4_metrics.json"
  run_step 3 3 "Actual MP4 segmentation inference" \
    "1024프레임을 처리하고 True·False를 모두 발행하며 판단 불가 프레임은 발행하지 않는다" \
    pytest_suite "$root/mp4_runtime_loader.xml" \
    "$SCRIPT_DIR/integration/test_v3_mp4_runtime_loader.py"
  local rc=$?
  unset PART5_RUN_V3_MP4_INTEGRATION PART5_INTEGRATION_METRICS_PATH
  return "$rc"
}

run_benchmark_command() {
  "$PYTHON_BIN" -m research.integration_benchmark.benchmark "$@"
}

run_benchmark() {
  local root="$RUN_ROOT/integration_benchmark"
  local video="$PACKAGE_ROOT/research/data/videos/test_input_sequence1.mp4"
  local config="$PACKAGE_ROOT/research/configs/test_input_sequence1_methods_v1.json"
  local timeline="$PACKAGE_ROOT/research/configs/test_input_sequence1_timeline_v1.json"
  local method index=0

  for method in detection segmentation yolo_pose mediapipe_pose; do
    index=$((index + 1))
    run_step "$index" 6 "$method individual benchmark" \
      "1024프레임 개별 추론·ROS 발행·CSV·비교 영상·rosbag 생성이 완료된다" \
      run_benchmark_command \
      --video "$video" --config "$config" --timeline "$timeline" \
      --output-dir "$root/individual/$method" --mode individual --method "$method" \
      || return 1
  done

  run_step 5 6 "Four-method combined benchmark" \
    "동일 1024프레임에서 4개 모델 비교 산출물이 완료된다" \
    run_benchmark_command \
    --video "$video" --config "$config" --timeline "$timeline" \
    --output-dir "$root/all_modules" --mode all || return 1

  export PART5_VERIFY_V3_BENCHMARK_OUTPUTS=1
  export PART5_V3_BENCHMARK_RESULT_ROOT="$root"
  run_step 6 6 "Benchmark artifact contract" \
    "개별·전체 실행의 프레임 수, 지표와 rosbag 메시지 수가 일치한다" \
    pytest_suite "$root/pytest.xml" "$SCRIPT_DIR/integration_benchmark"
  local rc=$?
  unset PART5_VERIFY_V3_BENCHMARK_OUTPUTS PART5_V3_BENCHMARK_RESULT_ROOT
  return "$rc"
}

append_experiment_name() {
  local input_name=${PART5_EXPERIMENT_NAME:-}
  local safe_name target

  if [[ -t 0 ]]; then
    printf '\n실험명을 입력하세요(선택, Enter 시 생략): '
    IFS= read -r input_name || input_name=
  fi
  [[ -n "$input_name" ]] || return 0

  safe_name=$(printf '%s' "$input_name" | sed -E \
    's#[/\\]+#_#g; s/[[:space:]]+/_/g; s/[^[:alnum:]_.-]+/_/g; s/^[_ .-]+//; s/[_ .-]+$//')
  if [[ -z "$safe_name" ]]; then
    printf '[EXPERIMENT NAME] 유효한 문자가 없어 이름을 추가하지 않습니다.\n'
    return 0
  fi
  target="${RUN_ROOT}_${safe_name}"
  if [[ -e "$target" ]]; then
    printf '[EXPERIMENT NAME] 대상이 이미 존재해 이름을 추가하지 않습니다: %s\n' "$target"
    return 0
  fi
  mv -- "$RUN_ROOT" "$target"
  RUN_ROOT=$target
  printf '[EXPERIMENT NAME] %s\n' "$safe_name"
}

printf '[RUN START] Part 5 v1.3 validation\n'
printf '[CONFIG] profile=%s python=%s domain_id=%s\n' "$PROFILE" "$PYTHON_BIN" "$ROS_DOMAIN_ID"
printf '[PLAN] planned_suites=%s\n' "$PLANNED_SUITES"
printf '[ARTIFACTS] %s\n' "$RUN_ROOT"

set +e
"run_$PROFILE"
run_rc=$?
set -e

append_experiment_name
if ((SUITES_STARTED != PLANNED_SUITES || SUITES_FINISHED != PLANNED_SUITES || SUITES_FAILED != 0)); then
  run_rc=1
fi
run_status=PASS
if ((run_rc != 0)); then
  run_status=FAIL
fi

set +e
"$PYTHON_BIN" "$SCRIPT_DIR/validation_summary.py" \
  --profile "$PROFILE" --run-root "$RUN_ROOT" --status "$run_status" \
  --planned "$PLANNED_SUITES" --started "$SUITES_STARTED" \
  --finished "$SUITES_FINISHED" --passed "$SUITES_PASSED" --failed "$SUITES_FAILED"
summary_rc=$?
set -e
if ((summary_rc != 0)); then
  printf '[SUMMARY ERROR] validation_summary.py exited with %s\n' "$summary_rc" >&2
  run_rc=1
fi

exit "$run_rc"
