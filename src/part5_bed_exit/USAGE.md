# Part 5 침대 이탈 감지 사용법

ROS 노드는 웹캠 BGR 프레임을 선택한 판단 객체의 `judge(frame) -> bool`에 전달한다. 최근 5개 유효 프레임 중 3개 이상이 `True`이면 `/patient/bed_exit`에 `True`를 발행한다. 카메라 또는 판단 실패 시 이력을 초기화하고 해당 프레임은 발행하지 않는다.

기본 실행은 외장 USB 웹캠을 우선 선택하고 `SegmentationBedExitMethod`를 사용한다. 추론 장치는 CUDA를 사용할 수 있으면 `cuda:0`, 아니면 CPU로 자동 선택한다. GUI 환경에서는 ROI·마스크·5프레임 투표 프리뷰도 표시한다.

## 런타임 구성

- `bed_exit_node.py`: 카메라 입력, 프리뷰, 5프레임 투표, ROS 발행
- `bed_exit_method.py`: `judge(frame) -> bool` 계약과 메서드 로딩
- `bed_exit_state.py`: 최근 5프레임 3회 이상 투표
- `pretrained.py`: 네 내장 모델 생성과 단일 ROI 설정 검증
- `methods/bed_exit_core.py`: Detection, Segmentation, YOLO Pose, MediaPipe Pose 구현
- `methods/detection_method.py`, `methods/segmentation_method.py`: 기본 ROI와 무인자 생성자를 지원하는 Detection/Segmentation 구현

## 메서드 선택

기본 Segmentation 외에도 ROS 파라미터로 다른 구현을 지정할 수 있다.

- 클래스 경로: `-p method:=your_package.your_method:YourBedExitMethod`
- 기본 Detection: `-p method:=part5_bed_exit.methods.detection_method:DetectionBedExitMethod`
- 내장 프리셋: `detection`, `segmentation`, `yolo_pose`, `mediapipe_pose` 중 하나와 검수된 `method_config` JSON 지정
- 코드 직접 주입: `BedExitNode(detector=YourBedExitMethod())`

모든 방법론은 `judge(frame) -> bool`을 제공해야 한다. 내장 프리셋은 `examples/methods.template.json`을 로컬 설정으로 복사하고 실제 카메라 ROI를 검수한 뒤 `roi_reviewed=true`로 설정해야 한다.

### 메서드별 구현 · 기본값 차이

| 구분 | 클래스 / 경로 | 기반 추상 클래스 (다형성) | 기본 가중치 / ROI 키 | 주요 기본 파라미터 · 판정 규칙 |
| --- | --- | --- | --- | --- |
| **Segmentation (기본)** | `SegmentationBedExitMethod` (`methods/segmentation_method.py`) | `BaseYoloBedExitMethod(BedExitMethod)` | `yolo11n-seg.pt` / `bed_polygon` (`methods/bed_roi.json`) | `outside_threshold=0.05`, `conf=0.25`, `imgsz=640`, `device='auto'`, `max_center_jump=0.25`, `mask_max_dim=160` |
| **Detection** | `DetectionBedExitMethod` (`methods/detection_method.py`) | `BaseYoloBedExitMethod(BedExitMethod)` | `yolo11n.pt` / `bed_polygon` (`methods/bed_roi.json`) | `outside_threshold=0.25`, `require_center_inside=True`, `conf=0.35`, `imgsz=480`, `device='cpu'` |
| **YOLO Pose / MediaPipe (프리셋)** | `YoloPoseROI`, `MediaPipePoseROI` (`methods/bed_exit_core.py`) | `bed_exit_core.BedExitMethod(BedExitMethod)` | `yolo11n-pose.pt` / `pose_landmarker_full.task` / `roi` (`method_config` JSON) | `policy='any_body'`, `point_conf=0.5`, `tolerance_px=3.0` (공통 13관절 중 1개라도 밖이면 `True`, 누락 시 `JudgmentUnavailable`) |
| **미구현 스타터 템플릿** | `BedExitMethodTemplate` (`method_template.py`, `research/templates/method_template.py`) | `BedExitMethod` | 없음 | `judge()` 호출 시 `NotImplementedError` 발생 (팀원 복사·상속용) |

## 빌드와 실행

ROS 2 Jazzy를 source한 뒤 워크스페이스 루트에서 실행한다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/part5_bed_exit/requirements.txt

source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit
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

실행 옵션 예시:

```bash
# 카메라 번호 직접 지정 및 프리뷰 비활성화
ros2 run part5_bed_exit bed_exit --ros-args \
  -p camera_index:=0 \
  -p show:=false

# Detection 클래스 선택
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=part5_bed_exit.methods.detection_method:DetectionBedExitMethod

# 검수된 ROI를 사용하는 YOLO Pose 내장 프리셋
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=yolo_pose \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json"
```

`capture_fps`는 프레임 수집 시도 주기이며 실제 처리율은 카메라와 추론 시간에 제한된다.

## 침대 ROI와 추론 장치

기본 ROI는 `part5_bed_exit/methods/bed_roi.json`의 `bed_polygon`에 정규화 좌표로 저장된다. 다른 ROI는 환경변수로 지정할 수 있다.

```bash
export BED_EXIT_ROI_CONFIG=/path/to/roi.json
export BED_EXIT_DEVICE=cpu       # 또는 cuda:0
```

기본 Segmentation 임계값은 ROI 밖 마스크 비율 5%이며 생성자의 `outside_threshold`로 변경할 수 있다. Detection/Segmentation 생성자에서 `conf`, `imgsz`, `device`, 추적 관련 파라미터도 명시적으로 덮어쓸 수 있다.

## ROS 2 없이 비교·시험

영상/웹캠 5프레임 다수결 비교:

```bash
python3 src/part5_bed_exit/research/evaluation/compare_methods.py
python3 src/part5_bed_exit/research/evaluation/compare_methods.py \
  --source test_bed.mp4 \
  --roi /path/to/roi.json
```

단일 프레임 순전파·시각화 비교:

```bash
python3 src/part5_bed_exit/compare_single_frame.py
python3 src/part5_bed_exit/compare_single_frame.py \
  --source img1.jpg img2.png \
  --show
```

단위 테스트:

```bash
PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q
```

데이터셋 다운로드, 노트북, 메서드 비교, 이벤트 평가는 `research/`에 분리되어 있으며 ROS 패키지 설치 대상이 아니다. 모델 가중치와 로컬 설정도 Git에서 제외된다.
