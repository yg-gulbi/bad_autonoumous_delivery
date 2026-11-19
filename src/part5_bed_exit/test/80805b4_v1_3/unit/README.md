# 80805b4 단위 테스트 계획

## 기준

- 기준 커밋: `80805b4` (`fix(part5): restore runtime method loading`)
- 테스트 파일명은 검증하는 생산 코드 모듈명과 일치시킨다.
- 한 테스트는 한 클래스 또는 함수의 공개 동작만 검증한다.
- ROS, 카메라, 모델, 파일 시스템 경계는 Stub·Monkeypatch·임시 파일로 격리한다.
- 실제 MP4, 가중치 추론, DDS 통신, 처리 속도 측정은 단위 테스트에서 제외한다.

## 테스트 대상

| 생산 코드 | 코드 단위 | 테스트 파일 | 입력·대역 | 예상 출력 |
| --- | --- | --- | --- | --- |
| `bed_exit_state.py` | `BedExitState.update()` | `test_bed_exit_state.py` | Bool 시퀀스 | 5프레임 중 True 3회 이상일 때만 True |
| `bed_exit_method.py` | `BedExitMethod`, `validate_bed_exit_method()`, `load_bed_exit_method()` | `test_bed_exit_method.py` | 가짜 판단 객체·가짜 import 모듈·가짜 팩터리 | 인터페이스 검증 및 선택 대상 객체 반환 |
| `pretrained.py` | `read_config()`, `create_method()` | `test_pretrained.py` | 임시 JSON·가짜 모델 생성자 | ROI·모델 경로 해석 및 선택된 모델 객체 생성 |
| `methods/bed_exit_core.py` | `PolygonROI`, `PatientSelector`, `BedExitMethod.judge()`, `YoloMethod.candidates()`, 모델별 `decide()`·`render()` | `test_bed_exit_core.py` | NumPy 프레임·가짜 YOLO 출력 | 기하 계산, 환자 선택, 판단 불가, Bool 판정, 시각화 |
| `bed_exit_node.py` | `BedExitNode.__init__()`, `process_frame()`, 자원 정리 메서드 | `test_bed_exit_node.py` | 가짜 카메라·판단 객체·발행자·동적 로더 | 파라미터 전달, 30 Hz 타이머, 발행·미발행·이력 초기화 |

## 실행

```bash
cd ~/workspaces/bad_autonoumous_delivery
source /home/hyunwoong/venvs/rokey_venv/bin/activate
source /opt/ros/jazzy/setup.bash
source install/setup.bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=98
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST

mkdir -p src/part5_bed_exit/results/unit_runs/YYYYMMDD_HHMMSS_part5_unit/ros_logs
ROS_LOG_DIR=$PWD/src/part5_bed_exit/results/unit_runs/YYYYMMDD_HHMMSS_part5_unit/ros_logs \
python3 -m pytest src/part5_bed_exit/test/80805b4_v1_3/unit -q \
  --junitxml=src/part5_bed_exit/results/unit_runs/YYYYMMDD_HHMMSS_part5_unit/part5_unit_junit.xml
```

통과 조건은 전체 테스트 성공과 테스트 종료 후 실제 카메라·모델·DDS 자원이 생성되지 않는 것이다.
실행 결과는 `results/unit_runs/<실행 시각>_part5_unit/part5_unit_junit.xml`에서
테스트별로 확인한다. `YYYYMMDD_HHMMSS`는 실제 실행 시각으로 바꿔서 사용한다.
