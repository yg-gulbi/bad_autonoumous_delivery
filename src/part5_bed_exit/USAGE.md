# Part 5 침대 이탈 감지 사용법

ROS 노드는 웹캠 BGR 프레임을 선택한 판단 객체의 `judge(frame) -> bool`에 전달한다. 최근 5개 유효 프레임 중 3개 이상이 `True`이면 `/patient/bed_exit`에 `True`를 발행한다. 카메라 또는 판단 실패 시 이력을 초기화하고 해당 프레임은 발행하지 않는다.

운영 실행은 `camera_index=0`의 웹캠과 `method` 또는 `method_config`로 선택한 판단 모델을 사용한다. 설정에는 실제 카메라에서 정한 단일 ROI와 모델 디렉터리도 함께 넣는다. 다른 카메라는 `camera_index`로 명시한다.

## 런타임 구성

- `bed_exit_node.py`: 카메라 입력, 프리뷰, 5프레임 투표, ROS 발행
- `bed_exit_method.py`: `judge(frame) -> bool` 계약, 동적 로더와 객체 검사
- `bed_exit_state.py`: 최근 5프레임 3회 이상 투표
- `pretrained.py`: 설정의 `method`에 맞는 판단 객체 생성과 ROI 설정 검증
- `methods/bed_exit_core.py`: 네 판단 모델의 실제 구현

## 운영 메서드

운영 노드는 `method` ROS 파라미터로 내장 모델 이름이나 임의의
`package.module:ClassOrObject`를 선택할 수 있다. `method`가 비어 있으면
`method_config` JSON의 `method`로 네 내장 모델 중 하나를 선택한다.

- 운영 실행: 검수된 `method_config` JSON 지정
- 내장 방식 명시 선택: `-p method:=segmentation -p method_config:=...`
- 외부 방식 동적 로딩: `-p method:=my_package.my_method:CustomMethod`
- 단위 테스트: `BedExitNode(detector=FakeDetector())` 형태의 코드 직접 주입
- 네 모델 비교: `research/`의 벤치마크 도구에서만 수행

판단 객체는 `judge(frame) -> bool`을 제공해야 한다. `config/methods.template.json`을 로컬 설정으로 복사한 뒤 실제 카메라의 ROI 좌표로 교체한다.

### 메서드별 구현 · 기본값 차이

| 구분 | 클래스 / 경로 | 기반 추상 클래스 (다형성) | 기본 가중치 / ROI 키 | 주요 기본 파라미터 · 판정 규칙 |
| --- | --- | --- | --- | --- |
| **Segmentation (기본 선택)** | `SegmentationROI` (`methods/bed_exit_core.py`) | `bed_exit_core.BedExitMethod(BedExitMethod)` | `yolo11n-seg.pt` / `roi` (`method_config` JSON) | 마스크 값 `>0.5`, ROI 밖 비율 `>0.05`, 검수된 ROI 필수 |

## 빌드와 실행

ROS 2 Jazzy를 source한 뒤 워크스페이스 루트에서 실행한다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/part5_bed_exit/requirements.txt

source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash

# 실제 USB 카메라 화면을 기준으로 roi 좌표를 수정한다.
cp src/part5_bed_exit/config/methods.template.json \
  src/part5_bed_exit/config/methods.local.json

ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json"
```

외부 자식 클래스는 무인자 생성 가능해야 하며 `judge(frame) -> bool`을 제공해야 한다.
외부 방식에는 `method_config`를 함께 지정하지 않는다.

```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=my_package.my_method:CustomMethod
```

로봇 Discovery Server 없이 PC에서 단독 시험할 때는 실행 전에 다음 환경 변수를 해제한다.

```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
```

토픽 확인:

```bash
ros2 daemon stop
ros2 daemon start
ros2 topic echo /patient/bed_exit
```

### 영상 파일(`.mp4`)로 ROS 2 노드 실행하기
웹캠 대신 녹화한 영상 파일을 넣어도 동일하게 화면 표시와 `/patient/bed_exit` 토픽
발행이 동작한다. 영상 끝에 도달하면 기본적으로 타이머를 중단하며,
`loop_video:=true`를 지정하면 투표·추적 상태를 초기화한 뒤 첫 프레임부터 반복한다.

```bash
# 방법 1: 뒤에 영상 파일 경로 바로 전달
ros2 run part5_bed_exit bed_exit test_bed.mp4

# 방법 2: ROS 파라미터로 전달
ros2 run part5_bed_exit bed_exit --ros-args -p video_path:=test_bed.mp4
```

```bash
# 통합 시험에서 MP4를 계속 반복
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json" \
  -p video_path:="$(pwd)/test_bed.mp4" \
  -p loop_video:=true
```

### 자주 쓰는 실행 옵션 (필요할 때만 추가)
```bash
# 카메라 번호 직접 지정(예: 0번 내장캠) 또는 프리뷰 창 끄기
ros2 run part5_bed_exit bed_exit --ros-args -p camera_index:=0 -p show:=false
```

```bash
# 카메라 번호 직접 지정 및 프리뷰 비활성화
ros2 run part5_bed_exit bed_exit --ros-args \
  -p camera_index:=0 \
  -p show:=false \
  -p method_config:="$(pwd)/src/part5_bed_exit/config/methods.local.json"

```

`capture_fps`는 프레임 수집 시도 주기이며 실제 처리율은 카메라와 추론 시간에 제한된다.

## 침대 ROI와 추론 장치

운영 판단 모델은 `method_config`의 정규화된 `roi` 좌표를 공통으로 사용한다. `methods/bed_roi.json`은 연구 도구 호환 구현의 예시이므로 실제 배포 ROI로 간주하지 않는다.

```bash
export BED_EXIT_ROI_CONFIG=/path/to/roi.json
export BED_EXIT_DEVICE=cpu       # 또는 cuda:0
```

기본 Segmentation 임계값은 ROI 밖 마스크 비율 5%이며 생성자의 `outside_threshold`로 변경할 수 있다. Detection/Segmentation 생성자에서 `conf`, `imgsz`, `device`, 추적 관련 파라미터도 명시적으로 덮어쓸 수 있다.

## ROS 2 없이 비교·시험

### 운영 노드와 분리한 MP4 비교 테스트

운영 `bed_exit` 노드도 `video_path`를 지원하지만, 모델별 비교 결과가 필요할 때는
`research/evaluation/video_comparison.py`가 파일을 직접 디코딩해 같은
`judge(frame)` 판정과 5프레임 3회 다수결을 적용한다. 이 비교 도구는 운영 ROS 토픽을
발행하지 않고 CSV와 비교 영상을 생성한다.

Segmentation 하나만 시험:

```bash
PYTHONPATH=src/part5_bed_exit python \
  -m research.evaluation.video_comparison \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --output-dir src/part5_bed_exit/results/exploratory/video_comparison_runs/test_input_sequence1_segmentation_run \
  --methods segmentation
```

네 모델을 같은 MP4로 비교하려면 `--methods` 옵션을 생략한다. 결과는 지정한
폴더의 `comparison.csv`(모델별 요약표), `frames.csv`(프레임별 판정),
`comparison.mp4`(판정 시각화), `run.json`(실행 메타데이터)에 저장된다.

### MP4 4모델 비교 + ROS 토픽 + bag 일괄 실행

아래 래퍼는 MP4를 한 번 디코딩해 네 모델에 같은 프레임을 전달하고, 모델별
토픽을 발행하면서 내부에서 `ros2 bag record`를 시작·종료한다. 기존 모델과
운영 노드는 수정하지 않는다. 출력 디렉터리는 실행마다 새 이름을 사용해야 한다.

```bash
source /opt/ros/jazzy/setup.bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0
export PYTHONPATH="$PWD/src/part5_bed_exit:${PYTHONPATH}"

env MPLCONFIGDIR=/tmp/part5-mpl \
  .venv/bin/python -m research.integration.four_method_mp4_benchmark \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --output-dir src/part5_bed_exit/results/integration_tests/mp4_four_model_benchmark/full_v2 \
  --methods detection segmentation yolo_pose mediapipe_pose \
  --final-method yolo_pose \
  --panel-width 320 \
  --show
```

모델별 토픽은 `/patient/bed_exit/<method>`이고, `--final-method` 결과만 기존
`/patient/bed_exit`에도 발행한다. 결과 디렉터리에는 `rosbag/`,
`comparison.csv`, `frames.csv`, `comparison.mp4`, `run.json`이 함께 저장된다.
Pose는 최초 정상 추적 이후 발생한 사람·관절 소실만 이탈 후보로 변환한다.
기존 판단 불가 정책으로 비교하려면 `--no-pose-loss-after-track`을 추가한다.

영상/웹캠 5프레임 다수결 비교:

```bash
python3 src/part5_bed_exit/research/evaluation/compare_methods.py
python3 src/part5_bed_exit/research/evaluation/compare_methods.py \
  --source test_bed.mp4 \
  --roi /path/to/roi.json
```

단일 프레임 순전파·시각화 비교:

```bash
python3 src/part5_bed_exit/research/evaluation/single_frame_comparison.py
python3 src/part5_bed_exit/research/evaluation/single_frame_comparison.py \
  --source img1.jpg img2.png \
  --show
```

단위 테스트:

```bash
PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q
```

데이터셋 다운로드, 노트북, 메서드 비교, 이벤트 평가는 `research/`에 분리되어 있으며 ROS 패키지 설치 대상이 아니다. 모델 가중치와 로컬 설정도 Git에서 제외된다.
