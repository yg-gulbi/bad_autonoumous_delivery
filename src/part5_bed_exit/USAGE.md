# Part 5 침대 이탈 감지 (`part5_bed_exit`) 사용법

## 1. 동작 요약
- **입력**: 웹캠 영상 (외장 USB 웹캠 연결 시 자동 우선 선택, 없으면 내장 카메라 `0`)
- **판정 (`SegmentationBedExitMethod`)**: GPU(`cuda:0`)에서 `yolo11n-seg.pt`로 환자 세그멘테이션 마스크를 추출해, 마스크가 침대 경계(ROI) 밖으로 **5% 초과** 나가면 `True(unsafe)`, 안쪽이면 `False(safe)` 반환
- **출력 토픽**: `/patient/bed_exit` (`std_msgs/msg/Bool`)
  - 최근 **5프레임 중 3프레임 이상 `True`** 이면 `data: true` (이탈), 아니면 `data: false` (정상)
  - 사람이 없거나 카메라/판정 예외 발생 시 이력을 초기화하고 토픽을 발행하지 않음

---

## 2. 빌드 및 ROS 2 노드 실행

**터미널 1 (노드 실행)**
```bash
cd ~/rokey_ws/bad_autonoumous_delivery
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT   # 로봇(Discovery Server) 없이 PC 단독 테스트 시
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit
```

**터미널 2 (토픽 확인)**
```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT   # 로봇 없이 PC 단독 테스트 시
ros2 daemon stop && ros2 daemon start
ros2 topic echo /patient/bed_exit
```

### 자주 쓰는 실행 옵션 (필요할 때만 추가)
```bash
# 카메라 번호 직접 지정(예: 0번 내장캠) 또는 프리뷰 창 끄기
ros2 run part5_bed_exit bed_exit --ros-args -p camera_index:=0 -p show:=false
```

---

## 3. 침대 경계(ROI) 수정
`part5_bed_exit/methods/bed_roi.json`에서 화면 비율(`0.0 ~ 1.0`)로 꼭짓점 좌표를 수정합니다 (`colcon build` 없이 노드 재실행 시 바로 반영).

```json
{
  "bed_polygon": [
    [0.2, 0.2],
    [0.8, 0.2],
    [0.8, 0.85],
    [0.2, 0.85]
  ]
}
```
- 다른 ROI JSON 파일을 지정하려면: `export BED_EXIT_ROI_CONFIG=/path/to/roi.json`

---

## 4. ROS 2 없이 웹캠·영상 테스트 및 단위 테스트

```bash
# 웹캠 바로 테스트 (USB 웹캠 자동 선택, 종료: q)
python3 src/part5_bed_exit/compare_methods.py

# 녹화한 영상 파일로 테스트
python3 src/part5_bed_exit/compare_methods.py --source test_bed.mp4

# 단위 테스트 실행
PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q
```
