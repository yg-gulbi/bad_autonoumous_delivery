#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
WORKSPACE=$(cd -- "$PACKAGE_ROOT/../.." && pwd)
RESULT_BASE="$PACKAGE_ROOT/results/1e0faac_v1_4"
PROFILE=${1:-}
RUN_ID=${PART5_RUN_ID:-$(date +%Y%m%d_%H%M%S)}

case "$PROFILE" in
  unit|integration) ;;
  *)
    echo "Usage: $0 {unit|integration}" >&2
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
  integration) PLANNED_SUITES=1 ;;
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

set +u
source /opt/ros/jazzy/setup.bash
if [[ -f "$WORKSPACE/install/setup.bash" ]]; then
  source "$WORKSPACE/install/setup.bash"
fi
set -u
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=${PART5_ROS_DOMAIN_ID:-104}
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
  run_step 1 1 "Part 5 v1.4 unit tests" \
    "TTS·파라미터화·앙상블·회전·파인튜닝 단위 테스트가 모두 통과한다" \
    pytest_suite "$RUN_ROOT/unit_tests/pytest.xml" "$SCRIPT_DIR/unit"
}

run_integration() {
  local root="$RUN_ROOT/integration_tests"
  mkdir -p "$root"

  run_step 1 1 "Async TTS, YAML/JSON parameterization and ensemble pipeline" \
    "비동기 반복 TTS(완료 보장·무한반복 방지), 파라미터 설정, 앙상블과 기존 v1.3 기본값이 함께 통과한다" \
    pytest_suite "$root/tts_and_params_pipeline.xml" \
    "$SCRIPT_DIR/integration/test_v4_tts_and_params_pipeline.py"
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

printf '[RUN START] Part 5 v1.4 validation\n'
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
