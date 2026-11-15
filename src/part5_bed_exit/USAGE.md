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
