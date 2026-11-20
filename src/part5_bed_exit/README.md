# PART 05 — Bed Exit Detection

별도 웹캠 영상으로 침대 이탈 여부를 판단하고 ROS 2 토픽
`/patient/bed_exit`에 `std_msgs/msg/Bool`을 발행한다. `True`는 이탈 감지,
`False`는 정상 상태다.

## 1. 우리 파트 실행

### 1.1 사전 준비

모든 명령은 워크스페이스 루트에서 실행한다.

```bash
cd ~/workspaces/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash
python -m pip install -r src/part5_bed_exit/requirements.txt
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
```

모델 가중치와 시험 영상은 Git 커밋 대상이 아니다. 가중치 4개는 공식 배포 URL에서
자동 다운로드할 수 있다.

```bash
PYTHONPATH=src/part5_bed_exit python -c \
  'from research.dataset_download_label.downloads import download_models; download_models("src/part5_bed_exit/models")'
```

다운로드 결과는 다음 위치에 저장되며, 각 파일 옆에 출처 URL과 SHA-256이 든
`*.source.json` 영수증이 생성된다.

```text
src/part5_bed_exit/models/yolo11n.pt
src/part5_bed_exit/models/yolo11n-seg.pt
src/part5_bed_exit/models/yolo11n-pose.pt
src/part5_bed_exit/models/pose_landmarker_full.task
```

시험 영상 `src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4`은 자동 다운로드
대상이 아니므로 별도로 전달받아 배치한다. 카메라·MP4 기본 실행은 `yolo11n-seg.pt`가
필요하고, 4모델 `benchmark`는 네 가중치가 모두 필요하다.

### 1.2 카메라 ROI 설정

카메라와 침대 위치를 고정한 뒤, 실제 카메라 영상에서 침대 경계 네 점을 지정한다.
`camera_index`에는 OpenCV가 인식하는 카메라 포트 번호를 넣는다.

```bash
ros2 run part5_bed_exit label_roi \
  --camera-index 0 \
  --output "$(pwd)/src/part5_bed_exit/config/methods.local.json"
```

기존 `methods.local.json`을 의도적으로 교체할 때만 `--force`를 추가한다.

```bash
ros2 run part5_bed_exit label_roi \
  --camera-index 0 \
  --output "$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  --force
```

ROI 라벨러 조작법은 다음과 같다.

- 왼쪽 클릭: 첫 클릭에서 화면을 고정하고 침대 경계점 추가
- 오른쪽 클릭: 마지막 점 취소
- `Space`: 고정 해제 후 실시간 화면으로 복귀
- `R`: 선택한 점 초기화
- `S`: 네 점을 정규화해 설정 JSON으로 저장
- `Q` 또는 `Esc`: 저장하지 않고 종료

네 점은 침대 외곽을 따라 서로 교차하지 않도록 순서대로 찍는다. 저장된
`config/methods.local.json`은 해당 카메라 구도 전용이다. 기준 MP4에는 이 파일을
사용하지 않고 MP4 전용 검수 설정을 사용한다.

### 1.3 MP4 입력 우회 실행

카메라 대신 저장소의 기준 MP4를 런타임 노드에 입력한다. MP4와 촬영 구도가 같은 전용
ROI 설정을 반드시 함께 사용한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json" \
  -p video_path:="$(pwd)/src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4" \
  -p capture_fps:=30.0 \
  -p output_topic:=/patient/bed_exit \
  -p loop_video:=true \
  -p show:=false
```

`loop_video:=true`이면 MP4를 반복하며 `Ctrl+C`로 종료한다. 한 번만 처리하려면
`loop_video:=false`로 바꾼다. 이 경우 영상 종료 후 타이머만 정지하므로
`Video playback finished` 로그를 확인한 다음 `Ctrl+C`로 노드를 종료한다.

정답 라벨인
`research/configs/test_input_sequence1_timeline_v1.json`은 런타임 노드 파라미터가
아니다. Part 5 통합 벤치마크가 정량평가할 때만 사용한다.

별도 터미널에서 최종 Bool 토픽을 확인할 수 있다.

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 topic echo /patient/bed_exit
```

### 1.4 실제 카메라 실행

ROI 라벨링에 사용한 고정 카메라와 같은 구도에서 실행한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  -p camera_index:=0 \
  -p capture_fps:=30.0 \
  -p output_topic:=/patient/bed_exit \
  -p show:=true
```

카메라 번호가 다르면 `camera_index`만 변경한다. 운영 중 OpenCV 프리뷰가 필요하지
않으면 `show:=false`로 실행한다. `video_path`를 함께 지정하면 카메라가 아니라 MP4 입력이
우선되므로 카메라 실행에서는 `video_path`를 넣지 않는다.

### 1.5 실행 파라미터

| 파라미터 | 형식 | 기본값 | 필요한 경우 | 역할 |
| --- | --- | --- | --- | --- |
| `method_config` | 문자열 | `""` | 내장 모델 실행 시 필수 | 모델 이름·가중치 경로·장치·ROI가 든 JSON |
| `method` | 문자열 | `""` | JSON 선택을 덮어쓸 때 | `detection`, `segmentation`, `yolo_pose`, `mediapipe_pose` 또는 외부 클래스 경로 |
| `camera_index` | 정수 | `0` | 실제 카메라 입력 | OpenCV 카메라 포트 번호 |
| `video_path` | 문자열 | `""` | MP4 입력 시 필수 | 값이 있으면 카메라 대신 MP4 사용 |
| `capture_fps` | 실수 | `30.0` | 선택 | 프레임 처리 시도 주기. 실제 FPS는 추론 속도에 제한됨 |
| `output_topic` | 문자열 | `/patient/bed_exit` | 선택 | `std_msgs/msg/Bool` 발행 토픽 |
| `loop_video` | 불리언 | `false` | MP4 반복 시 | MP4 EOF에서 프레임 0부터 다시 시작 |
| `show` | 불리언 | GUI 환경 여부 | 선택 | OpenCV 판정 프리뷰 표시 |

최소 필수 조합은 다음과 같다.

- 카메라: `method_config`와 올바른 `camera_index`
- MP4: MP4 전용 `method_config`와 `video_path`
- 내장 모델을 명령에서 직접 선택: `method`와 `method_config`
- 외부 플러그인: `method=package.module:ClassOrObject`; `method_config`는 사용하지 않음

`capture_fps:=30.0`은 30 FPS 처리를 보장하는 값이 아니라 프레임 처리 시도 주기다. 실제
처리율은 모델 추론 시간과 실행 장치 성능에 제한된다.

## 2. part3_part5 통합 테스트

PART 03 mock 관제와 PART 05 MP4 노드를 같은 ROS domain에서 실행해
`/patient/bed_exit=true` 이후 로봇 `PAUSE`와 `SAFE_PAUSE`를 검증한다. 핵심 5개 토픽은
MCAP rosbag으로 저장하며, 종료 후 bag을 다시 읽은 메시지 순서까지 통과해야 PASS다.

기록 대상은 다음과 같다.

```text
/ui/mission_req
/ui/ack
/robot2/hospital_nav/command
/fleet/state
/patient/bed_exit
```

```bash
source /opt/ros/jazzy/setup.bash
colcon build \
  --packages-select part3_fleet_manager part5_bed_exit \
  --symlink-install

bash part_integration/part3_part5/run.sh
```

실험명을 미리 지정하려면 다음과 같이 실행한다.

```bash
bash part_integration/part3_part5/run.sh --experiment-name part3_part5_final
```

실시간 관측 6개가 끝나면 7번째 `rosbag_evidence` 테스트가 MCAP을 다시 읽어 다음 인과
흐름을 검증한다.

6번째 판정 이후에는 최종 `SAFE_PAUSE + PAUSED + BED_EXIT` 상태를 3회 확인하는
`BAG SYNC` 구간을 거친다. recorder가 마지막 `/fleet/state`를 기록하기 전에 종료되는 것을
방지하기 위한 절차이며, 그 뒤에 MCAP을 닫고 7번째 판정을 수행한다.

```text
MISSION → ACK/MOVE → MOVING → bed_exit=false → bed_exit=true
                                             ├→ PAUSE
                                             └→ SAFE_PAUSE(BED_EXIT)
```

`PAUSE`와 `SAFE_PAUSE`는 서로 다른 토픽의 DDS 수신 시각이므로 둘 다
`bed_exit=true` 이후인지 검증하고 상호 기록 순서는 참고값으로 남긴다. 실시간 테스트가
통과해도 bag에 토픽이 없거나 위 인과 흐름을 증명하지 못하면 전체 결과는 FAIL이다.

정상 완료 시 콘솔에는 다음 항목이 출력된다.

```text
[PLAN] planned_tests=7
[TEST 7/7 START] rosbag_evidence
[TEST 7/7 END] PASS
bag_messages=...
bag_topic_counts=...
[RUN END] PASS
```

결과는 다음 위치에 저장된다.

```text
part_integration/part3_part5/results/80805b4_v1_3/YYYYMMDD_HHMMSS_part3_part5[_실험명]/
├── summary.json
├── bag_evidence.json
├── execution.log
├── rosbag_recorder.log
└── rosbag/
    ├── metadata.yaml
    └── *.mcap
```

`rosbag/`이 원본 통신 증거다. `bag_evidence.json`과 `summary.json`의 `bag_evidence`에는
토픽별 메시지 수, 검증에 사용한 상태 전이의 상대 시각과 전체 관측값이 기록된다.
콘솔과 `execution.log`에는 전체 `/fleet/state` 대신 핵심 8단계가 한 줄씩 출력된다. 상세 계약은
[`part_integration/part3_part5/README.md`](../../part_integration/part3_part5/README.md)를 참고한다.

## 3. Part 5 내부 테스트

세 프로필은 각각 별도로 실행한다. 스크립트가 ROS 환경과 현재 워크스페이스의
`install/setup.bash`를 소싱하며, OpenCV 창은 표시하지 않는다.

```bash
# 단위 테스트
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit

# 동적 로더·예외 안전·실제 MP4 통합 테스트
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh integration

# 4개 모델 개별 실행·전체 비교·정량평가
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh benchmark
```

각 suite는 콘솔에 `[START]`, `[EXPECTED]`, `[END]`를 표시한다. 전체 실행이 끝나면
정량 결과, PASS/FAIL, 산출물 경로를 출력하고 선택적으로 실험명을 입력받는다.

```text
src/part5_bed_exit/results/80805b4_v1_3/YYYYMMDD_HHMMSS_PROFILE[_실험명]/
```

`benchmark`는 CPU에서 1,024프레임을 여러 모델로 반복 추론하므로 수 시간이 걸릴 수
있다. MediaPipe의 `NORM_RECT without IMAGE_DIMENSIONS` 한 줄은 라이브러리 내부 경고이며,
후속 `[PROGRESS]`가 계속 출력되면 실행 중인 상태다.

세부 테스트 구성과 산출물 계약은
[`test/80805b4_v1_3/README.md`](test/80805b4_v1_3/README.md)를 참고한다. v1.3.1과
v1.3.2처럼 공개 계약을 변경하지 않는 patch 버전도 같은 테스트 트리를 사용한다.

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

## 필수 실행 조건

노드를 실행하려면 **판단 방법 하나**와 **영상 입력 하나**가 필요하다. MP4는 필수가
아니며, `video_path`를 지정하지 않으면 실제 카메라를 사용한다.

### 판단 방법

| 사용 방식 | `method` | `method_config` | 비고 |
| --- | --- | --- | --- |
| JSON 안의 내장 모델 사용 | 생략 가능 | **필수** | JSON의 `method`를 읽음 |
| 명령에서 내장 모델 지정 | **필수** | **필수** | `detection`, `segmentation`, `yolo_pose`, `mediapipe_pose` |
| 외부 클래스·객체 동적 로딩 | **필수** | 사용 금지 | `package.module:ClassOrObject` |
| detector 직접 주입 | 불필요 | 불필요 | Python 테스트 전용 |

내장 모델을 사용하는 일반적인 운영·통합 테스트에서는 `method_config`가 필수다.
`method`와 `method_config`를 모두 생략하면 노드가
`ValueError: Set method or method_config`로 시작을 중단한다.

### 영상 입력

| 입력 | 필수 파라미터 | 사용하지 않는 파라미터 |
| --- | --- | --- |
| 실제 카메라 | 없음 (`camera_index=0` 기본값) | `video_path` |
| 다른 번호의 카메라 | `camera_index` | `video_path` |
| 녹화 MP4 | `video_path` | `camera_index` |

실제 카메라와 MP4는 서로 다른 촬영 구도일 수 있으므로 각각 그 화면에 맞춰 검수한 ROI
설정 JSON을 사용해야 한다.

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
8개 값을 노드 시작 시 한 번 읽는다. 현재 구현은 실행 중 `ros2 param set`으로 값을
바꿔도 카메라나 판단 객체를 다시 생성하지 않으므로, 변경 후 노드를 재시작해야 한다.

| 파라미터 | 형식 | 기본값 | 필수 여부 | 용도 |
| --- | --- | --- | --- | --- |
| `method` | 문자열 | `""` | 조건부 | 내장 모델 이름 또는 `package.module:ClassOrObject` |
| `method_config` | 문자열 | `""` | 내장 모델 사용 시 필수 | ROI·모델 경로·장치가 든 JSON 경로 |
| `camera_index` | 정수 | `0` | 카메라 번호 변경 시 | OpenCV가 열 USB 카메라 번호 |
| `reconnect_interval` | 실수 | `1.0` | 선택 | 웹캠 미연결·끊김 시 재연결 시도 최소 간격(초) |
| `video_path` | 문자열 | `""` | MP4 입력 시 필수 | 웹캠 대신 한 번 재생할 MP4 경로 |
| `loop_video` | 불리언 | `false` | 선택 | MP4 종료 시 프레임 0부터 반복 |
| `output_topic` | 문자열 | `/patient/bed_exit` | 선택 | 최종 Bool 결과 발행 토픽 |
| `capture_fps` | 실수 | `30.0` | 선택 | 초당 프레임 처리 시도 횟수 |
| `show` | 불리언 | GUI 환경 존재 여부 | 선택 | OpenCV 판정 프리뷰 표시 |

입력 선택 규칙은 다음과 같다.

- `video_path`가 비어 있으면 `camera_index`의 카메라를 사용한다.
- `video_path`가 있으면 MP4를 사용하고 `camera_index`는 사용하지 않는다.
- MP4는 기본적으로 끝에서 정지한다. `loop_video=true`면 프레임 0부터 반복한다.
- 반복 경계에서는 5프레임 투표와 detector 추적 상태를 초기화한다.
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

실제 카메라용 로컬 설정을 처음 만들 때만 템플릿을 복사한다.

```bash
cp src/part5_bed_exit/config/methods.template.json \
  src/part5_bed_exit/config/methods.local.json
```

```json
{
  "method": "segmentation",
  "model_dir": "../models",
  "device": "cpu",
  "roi": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
}
```

- 위 ROI는 파일 형식을 보여주기 위한 **예시 사각형**이며 운영·통합 테스트용으로
  검수된 ROI가 아니다.
- 실제 카메라에서는 설치된 카메라 화면의 침대 경계를 기준으로
  `methods.local.json`의 `roi`를 수정한 뒤 실행한다.
- `model_dir`의 상대경로는 JSON 파일이 있는 디렉터리를 기준으로 해석한다.
- `device`에는 `cpu`, `cuda:0` 같은 추론 장치를 지정한다.
- `roi`에는 영상 크기에 대해 정규화한 실제 침대 다각형 좌표를 입력한다.
- 운영용 로컬 JSON과 모델 가중치는 Git에 커밋하지 않는다.

저장소의 기준 MP4에는 별도로 검수한 설정을 사용한다.

| 파일 | 경로 |
| --- | --- |
| 기준 영상 | `research/data/videos/test_input_sequence1.mp4` |
| 노드 실행용 설정 | `research/configs/test_input_sequence1_methods_v1.json` |
| ROI 근거·검수 기록 | `research/configs/test_input_sequence1_roi_v1.json` |

`test_input_sequence1_roi_v1.json`은 ROI 출처와 검수 정보를 기록하는 문서이며
`method_config`로 직접 전달하지 않는다. 노드에는
`test_input_sequence1_methods_v1.json`을 전달한다.

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

### 실제 카메라

MP4는 필요하지 않다. `methods.local.json`의 ROI를 실제 카메라 화면에 맞게 보정한 뒤
실행한다. JSON 안에 `method`가 있으므로 아래 최소 명령에서는 `method`를 생략한다.

카메라 화면에서 침대 경계 네 점을 직접 찍어 새 설정을 만들려면 빌드 후 ROI 라벨러를
실행한다.

```bash
ros2 run part5_bed_exit label_roi --camera-index 0 \
  --output "$(pwd)/src/part5_bed_exit/config/methods.local.json"
```

- 왼쪽 클릭: 첫 클릭에서 화면을 고정하고 침대 경계점을 순서대로 추가
- 오른쪽 클릭: 마지막 점 취소
- `Space`: 화면 고정 해제 후 실시간 화면으로 복귀
- `R`: 찍은 점 초기화
- `S`: 네 점을 정규화해 JSON으로 저장
- `Q` 또는 `Esc`: 저장하지 않고 종료

네 점은 침대 경계를 따라 교차하지 않도록 순서대로 찍는다. 기존 출력 파일을 의도적으로
교체할 때만 `--force`를 추가한다. 생성된 JSON은 바로 `method_config`로 사용할 수 있다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  -p camera_index:=0
```

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json"
```

판단 방법과 카메라 번호를 명시하려면 다음과 같이 실행한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  -p camera_index:=0 \
  -p show:=true
```

### 다른 USB 카메라

카메라 연결 위치가 바뀌면 `camera_index`만 변경한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  -p camera_index:=1 \
  -p show:=false
```

### JSON의 내장 모델 선택값 사용

기존 JSON 설정과 호환해야 할 때만 `method`를 생략한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
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

배포 통합 테스트에는 기준 영상과 그 영상용으로 검수한 설정을 함께 사용한다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json" \
  -p video_path:="$(pwd)/src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4" \
  -p loop_video:=true \
  -p show:=true
```

첫 번째 일반 인자로 MP4를 전달하는 기존 방식도 지원한다.

```bash
ros2 run part5_bed_exit bed_exit \
  "$(pwd)/src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4" \
  --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json"
```

MP4는 저장소 정책상 Git 커밋 대상이 아니다. 배포 환경에서는 위 상대경로에 별도
전달한 기준 영상을 배치하거나, `video_path`에 실제 배포 경로를 지정한다.

### 토픽 확인

```bash
ros2 topic echo /patient/bed_exit
```

## 실행 중 실패 정책

| 실패 지점 | 노드 동작 | 발행 | 투표 이력 |
| --- | --- | --- | --- |
| 시작 시 웹캠 미연결 | 노드를 유지하고 `reconnect_interval`(기본 1.0초)마다 재연결 시도 | 미발행 | 초기화 |
| `capture.read()` 예외 | 오류 기록 후 웹캠 핸들 해제(`release`) 및 쿨다운 재연결 대기 | 해당 프레임 미발행 | 초기화 |
| 웹캠의 잘못된 반환 | 오류 기록 후 웹캠 핸들 해제(`release`) 및 쿨다운 재연결 대기 | 해당 프레임 미발행 | 초기화 |
| MP4 종료 (`loop_video=false`) | 타이머 중단 | 추가 발행 없음 | 초기화 |
| MP4 종료 (`loop_video=true`) | 프레임 0부터 재시작 | 재워밍업 후 계속 발행 | 초기화 |
| MP4 되감기 실패 | 오류 기록 후 타이머 중단 | 추가 발행 없음 | 초기화 |
| `judge()` 예외 | 프레임 건너뜀 | 해당 프레임 미발행 | 초기화 |
| publisher 예외 | 다음 프레임부터 재시도 | 실패 결과 미발행 | 초기화 |
| 프리뷰 예외 | `show`만 비활성화 | 이미 계산한 결과 유지 | 유지 |

설정 JSON 오류, 알 수 없는 모델, import 불가, MP4 최초 열기 실패처럼 시작 구성이
잘못된 경우에는 잘못된 상태로 운영하지 않도록 초기화를 실패시킨다. 단, 실제 USB 웹캠은
시작 시 미연결이거나 실행 중 케이블이 뽑혀도 노드를 유지하고 `reconnect_interval`마다 자동 재연결한다.

## 시작 오류 확인

| 오류 | 원인 | 조치 |
| --- | --- | --- |
| `Set method or method_config` | 판단 방법을 모두 생략함 | 내장 모델 설정 JSON 또는 외부 모듈 경로 지정 |
| `Built-in methods require method_config` | 내장 이름만 지정함 | ROI가 포함된 `method_config` 추가 |
| `method_config applies only to built-in method names` | 외부 모듈과 JSON을 함께 지정함 | 외부 모듈 실행에서 `method_config` 제거 |
| `Cannot open video source` | 지정한 MP4 경로가 잘못되었거나 열 수 없음 | `video_path` 파일 경로 확인 |

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

Part 5 검증은 결과 로그와 정량 요약을 함께 생성하는 래퍼를 사용한다.

```bash
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh integration
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh benchmark
```

실험 결과는
`results/80805b4_v1_3/YYYYMMDD_HHMMSS_PROFILE[_실험명]/`에 저장한다. 세부 테스트
구성과 산출물 계약은 [v1.3 검증 문서](test/80805b4_v1_3/README.md)를 참고한다.
