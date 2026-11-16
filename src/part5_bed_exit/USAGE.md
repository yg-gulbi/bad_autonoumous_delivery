# Part 5 침대 이탈 런타임

ROS 노드는 웹캠 BGR 프레임을 선택된 판단 객체의 `judge(frame) -> bool`에 전달한다.
최근 5개 유효 프레임 중 3개 이상이 `True`이면 `/patient/bed_exit`에 `True`를 발행한다.
카메라 또는 판단 실패 시 이력을 초기화하고 해당 프레임은 발행하지 않는다.

## 런타임 구성

- `bed_exit_node.py`: 카메라 입력, 5프레임 투표, ROS 발행
- `bed_exit_method.py`: `judge(frame) -> bool` 계약과 메서드 로딩
- `bed_exit_state.py`: 최근 5프레임 3회 이상 투표
- `pretrained.py`: 네 내장 모델 생성과 ROI 설정 검증
- `methods/bed_exit_core.py`: Detection, Segmentation, YOLO Pose, MediaPipe Pose 구현

`method`는 `detection`, `segmentation`, `yolo_pose`, `mediapipe_pose` 중 하나다.
`method_config`에는 검수 완료한 단일 ROI 설정 JSON을 전달한다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/part5_bed_exit/requirements.txt

source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=yolo_pose \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p output_topic:=/patient/bed_exit \
  -p camera_index:=0 \
  -p capture_fps:=30.0
```

`examples/methods.template.json`을 `methods.local.json`으로 복사한 뒤 실제 카메라 ROI를
검수하고 `roi_reviewed=true`로 변경해야 한다. 모델 가중치와 로컬 설정은 Git에서 제외된다.

데이터셋 다운로드, 노트북, 메서드 비교, 이벤트 평가는 [`research/README.md`](research/README.md)에 분리되어 있으며 ROS 패키지 설치 대상이 아니다.

## 직접 import용 Detection·Segmentation 메서드 및 단일 프레임 시각화 비교 (`compare_single_frame.py`)

`method_config` JSON 없이 무인자 생성자와 기본 `methods/bed_roi.json`(또는 `BED_EXIT_ROI_CONFIG`, 이미지별 `<이름>.roi.json` 사이드카)으로 바로 실행·시각화할 수 있는 메서드도 함께 제공한다.

- `part5_bed_exit.methods.detection_method:DetectionBedExitMethod` (`yolo11n.pt`, 사람 BBox 중심점 ROI 포함 여부 + ROI 외부 비율 > `0.25`)
- `part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod` (`yolo11n-seg.pt`, 사람 마스크의 안전 ROI 밖 픽셀 비율 > `0.10`, `mask_max_dim=160` 축소 평가)

```bash
# test_images/ 폴더 이미지를 5프레임 다수결 없이 단일 프레임 판정하고 outputs/<method>/에 저장
python3 src/part5_bed_exit/compare_single_frame.py

# 특정 이미지 직접 지정 + 화면 표시
python3 src/part5_bed_exit/compare_single_frame.py --source img1.jpg img2.png --show
```
