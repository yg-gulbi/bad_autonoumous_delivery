# PART 05 — Bed Exit Detection

별도 웹캠 영상으로 침대 이탈 여부를 판단하고 ROS 2 토픽
`/patient/bed_exit`에 `std_msgs/msg/Bool`을 발행한다. `True`는 이탈 감지,
`False`는 정상 상태다.

## 실행 구조

운영 노드는 `BedExitNode`가 웹캠·MP4 입력, 판단 메서드, 5프레임 다수결과 ROS 발행을
연결한다. `video_path`가 비어 있으면 `camera_index`의 웹캠을 사용하고, 값이 있으면
해당 MP4를 사용한다. `method` ROS 파라미터에는 내장 방식 이름 또는
`package.module:ClassOrObject` 형식의 외부 모듈을 지정할 수 있다. 내장 방식은
`method_config` JSON에서 ROI, 모델 경로와 실행 장치를 읽는다.

### 판단 방법 선택

운영에서 선택하는 방식은 다음 두 범주다.

1. 내장 모델: `method=segmentation`처럼 이름을 명시하고 `method_config`에 JSON 경로를
   전달한다. `method`를 비우고 JSON 안의 `method`를 읽는 방식은 기존 설정 호환용이다.
2. 외부 플러그인: `method=package.module:ClassName`을 사용한다. 이미 생성된 객체를
   모듈 변수로 공개한 `package.module:object_name`도 지원하지만 클래스 방식이 기본이다.

`BedExitNode(detector=FakeDetector())`는 ROS 운영 설정이 아니라 모델 없이 노드 경계를
검증하기 위한 테스트 전용 주입 방식이다.

카메라 읽기, 판단, ROS 발행과 선택적 프리뷰가 실패하면 예외를 타이머 콜백 밖으로
전파하지 않는다. 실패 프레임은 발행하지 않으며, 판단 연속성이 깨진 경우 투표 이력을
초기화하고 다음 유효 프레임부터 5프레임을 다시 누적한다.

## 설치와 빌드

ROS 2 Jazzy가 설치된 워크스페이스 루트에서 실행한다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/part5_bed_exit/requirements.txt

source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
```

로봇 Discovery Server 없이 PC에서 단독 시험할 때는 기존 설정을 해제한다.

```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
```

## ROS 파라미터

ROS 파라미터는 노드를 다시 빌드하지 않고 실행 구성을 전달하는 값이다. PART 05는 아래
7개 값을 노드 시작 시 한 번 읽는다. 현재 구현은 실행 중 `ros2 param set`으로 값을
바꿔도 카메라나 판단 객체를 다시 생성하지 않으므로, 변경 후 노드를 재시작해야 한다.

| 파라미터 | 형식 | 기본값 | 용도 |
| --- | --- | --- | --- |
| `method` | 문자열 | `""` | 내장 모델 이름 또는 `package.module:ClassOrObject` |
| `method_config` | 문자열 | `""` | 내장 모델의 ROI·모델 경로·장치가 든 JSON 경로 |
| `camera_index` | 정수 | `0` | OpenCV가 열 USB 카메라 번호 |
| `video_path` | 문자열 | `""` | 웹캠 대신 한 번 재생할 MP4 경로 |
| `output_topic` | 문자열 | `/patient/bed_exit` | 최종 Bool 결과 발행 토픽 |
| `capture_fps` | 실수 | `30.0` | 초당 프레임 처리 시도 횟수 |
| `show` | 불리언 | GUI 환경 존재 여부 | OpenCV 판정 프리뷰 표시 |

입력 선택 규칙은 다음과 같다.

- `video_path`가 비어 있으면 `camera_index`의 카메라를 사용한다.
- `video_path`가 있으면 MP4를 사용하고 `camera_index`는 사용하지 않는다.
- MP4는 끝에서 정지하며 자동 반복하지 않는다. `loop_video` 파라미터는 없다.
- `capture_fps`는 타이머 주기이며 실제 처리 속도는 카메라와 추론 속도에 제한된다.

## 판단 모듈 선택

표현은 다섯 가지지만 운영 선택은 내장 모델과 외부 플러그인 두 범주다.

| 표현 | 사용 위치 | 의미 |
| --- | --- | --- |
| 내장 모델 이름 + `method_config` | 운영 권장 | 실행 명령에서 모델을 명확히 선택 |
| JSON의 `method` + `method_config` | 기존 설정 호환 | `method` 파라미터가 비었을 때 JSON 값 사용 |
| `package.module:ClassName` | 외부 플러그인 권장 | 클래스를 import한 뒤 무인자 생성 |
| `package.module:object_name` | 외부 플러그인 고급 | 모듈에 이미 생성된 객체를 그대로 사용 |
| `BedExitNode(detector=...)` | 테스트 전용 | ROS 파라미터 없이 가짜 판단 객체 직접 주입 |

### 내장 모델과 설정 JSON

지원하는 내장 이름은 `detection`, `segmentation`, `yolo_pose`,
`mediapipe_pose`다. 내장 모델에는 `method_config`가 반드시 필요하다.

```bash
cp src/part5_bed_exit/examples/methods.template.json \
  src/part5_bed_exit/examples/methods.local.json
```

```json
{
  "method": "segmentation",
  "model_dir": "../models",
  "device": "cpu",
  "roi": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
}
```

- `model_dir`의 상대경로는 JSON 파일이 있는 디렉터리를 기준으로 해석한다.
- `device`에는 `cpu`, `cuda:0` 같은 추론 장치를 지정한다.
- `roi`에는 영상 크기에 대해 정규화한 실제 침대 다각형 좌표를 입력한다.
- 운영용 로컬 JSON과 모델 가중치는 Git에 커밋하지 않는다.

### 외부 모듈 계약

외부 클래스는 Python에서 import 가능하고 인자 없이 생성할 수 있어야 한다. 필수 계약은
OpenCV BGR 프레임을 받아 Python `bool`을 반환하는 `judge(frame)`이다.

```python
class CustomMethod:
    def __init__(self):
        pass

    def judge(self, frame) -> bool:
        return False

    def render(self, frame):       # 선택 사항
        return frame

    def close(self) -> None:       # 선택 사항
        pass
```

이미 생성한 객체를 모듈 변수로 공개할 수도 있지만 객체 상태가 공유될 수 있으므로 운영
확장에는 클래스 방식을 권장한다. 외부 모듈에는 `method_config`를 함께 전달하지 않는다.

## 모듈 실행 방법

모든 명령은 빌드 후 워크스페이스 루트에서 실행한다.

### 내장 모델과 기본 카메라

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p camera_index:=0 \
  -p show:=true
```

### 다른 USB 카메라

카메라 연결 위치가 바뀌면 `camera_index`만 변경한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p camera_index:=1 \
  -p show:=false
```

### JSON의 내장 모델 선택값 사용

기존 JSON 설정과 호환해야 할 때만 `method`를 생략한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p camera_index:=0
```

### 외부 클래스 동적 로딩

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=my_package.my_method:CustomMethod \
  -p camera_index:=0 \
  -p show:=false
```

### 외부 객체 동적 로딩

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=my_package.my_method:custom_method \
  -p camera_index:=0 \
  -p show:=false
```

### MP4 입력

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p video_path:="$(pwd)/test_bed.mp4" \
  -p show:=true
```

첫 번째 일반 인자로 MP4를 전달하는 기존 방식도 지원한다.

```bash
ros2 run part5_bed_exit bed_exit "$(pwd)/test_bed.mp4" --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json"
```

### 토픽 확인

```bash
ros2 topic echo /patient/bed_exit
```

## 실행 중 실패 정책

| 실패 지점 | 노드 동작 | 발행 | 투표 이력 |
| --- | --- | --- | --- |
| `capture.read()` 예외 | 오류 기록 후 다음 프레임 대기 | 해당 프레임 미발행 | 초기화 |
| 웹캠의 잘못된 반환 | 오류 기록 후 다음 프레임 대기 | 해당 프레임 미발행 | 초기화 |
| MP4 종료 | 타이머 중단 | 추가 발행 없음 | 초기화 |
| `judge()` 예외 | 프레임 건너뜀 | 해당 프레임 미발행 | 초기화 |
| publisher 예외 | 다음 프레임부터 재시도 | 실패 결과 미발행 | 초기화 |
| 프리뷰 예외 | `show`만 비활성화 | 이미 계산한 결과 유지 | 유지 |

설정 JSON 오류, 알 수 없는 모델, import 불가, 카메라 최초 열기 실패처럼 시작 구성이
잘못된 경우에는 잘못된 상태로 운영하지 않도록 초기화를 실패시킨다.

상세 연구·벤치마크 실행법은 [USAGE.md](USAGE.md)를 참고한다.

## 테스트 버전 규칙

테스트 기준선은 `test/<짧은-커밋>_vX_X/` 형식으로 관리한다. `X_X`는 기능 계약이
변경된 major/minor 기준선이며, patch 변경은 같은 `vX_X` 아래에 회귀 테스트를
추가한다. 예를 들어 v1.3.2의 회귀 테스트는 `80805b4_v1_3/`에 추가한다.

각 기준선은 필요에 따라 다음 범위를 가진다.

- `unit/`: 해당 기준선의 내부 클래스와 함수 단위 검증
- `integration/`: 카메라·MP4 입력부터 로더, 투표와 ROS 발행 경계까지 검증
- `integration_benchmark/`: 공통 영상·라벨·지표·산출물 계약 검증

v1.3.2의 `test_v3_mp4_replay_resilience.py`는 연구용 MP4 ROS 노드에
`capture.read()`·publisher·프리뷰 예외를 주입하고, 예외가 콜백 밖으로 전파되지 않는지
검증한다. 모델 정확도 테스트가 아니라 노드 생존성과 실패 프레임 미발행 정책을 확인하는
통합 회귀 테스트다.

## 통합 코드 재사용 계약

내부 리팩터링과 patch 변경은 기존 통합 테스트와 통합 벤치마크 코드를 수정하지 않고
다시 실행할 수 있어야 한다. 이를 위해 다음 공개 계약을 유지한다.

- 운영 로더: `load_bed_exit_method(method, method_config)`
- 판단 객체: `judge(frame) -> bool`, 선택적 `render(frame)`·`close()`
- 투표 상태: `BedExitState.update(bool) -> bool`
- ROS 출력: `/patient/bed_exit`, `std_msgs/msg/Bool`
- 벤치마크 입력: 기존 MP4·설정 JSON·타임라인 JSON
- 벤치마크 출력: `run.json`, `frames.csv`, `comparison.csv`, `comparison.mp4`, `rosbag/`

내부 모듈 이동이나 예외 처리 보강만으로 이 계약을 바꾸지 않는다. 계약을 깨는 변경은
새 `vX_X` 기준선을 만들고, 기존 통합 코드가 재사용되지 않는 이유와 마이그레이션 방법을
문서화해야 한다.

## 검증 명령

```bash
source /opt/ros/jazzy/setup.bash
ROS_LOG_DIR=/tmp/part5_test_logs \
PYTHONPATH="src/part5_bed_exit:${PYTHONPATH}" \
/usr/bin/python3 -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/unit \
  src/part5_bed_exit/test/80805b4_v1_3/integration \
  src/part5_bed_exit/test/80805b4_v1_3/integration_benchmark -q
```

실제 모델·1,024프레임 MP4·기존 벤치마크 산출물 검증은
[v1.3 통합 벤치마크 실행 문서](test/80805b4_v1_3/integration_benchmark/README.md)의
환경 변수와 명령을 사용한다.

실험 결과는 `results/<기준선커밋>_vX_X/<실험대상커밋>_vX_X_X/`에 저장한다.
v1.3.2 결과 경로는 `results/80805b4_v1_3/b08be95_v1_3_2/`다.
