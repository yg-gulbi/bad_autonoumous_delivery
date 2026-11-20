# Part 5 최종 실험 · 구동 명령어 정리

모든 명령은 워크스페이스 루트(`cd ~/rokey_ws/bad_autonoumous_delivery`)로 이동한 뒤 실행합니다.

---

## 1. 환경 설정 및 빌드

```bash
cd ~/rokey_ws/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
```

*(Part 3 + Part 5 연동 테스트 시 함께 빌드)*
```bash
colcon build --packages-select part3_fleet_manager part5_bed_exit --symlink-install
```

---

## 2. 파일 배치 위치 요약 (`src/part5_bed_exit/` 기준)

| 구분 | 배치 경로 | Git 추적 | 비고 |
| :--- | :--- | :---: | :--- |
| **모델 가중치** (`.pt`, `.task`) | `models/` | 제외 | `yolo11n-seg.pt`, `yolo11s-seg.pt` 등 |
| **ROI JSON · YAML · TTS 음원** | `config/` | 템플릿·`mp3`만 | `cam_roi.json`, `cam_roi_test.json`, `bed_exit.params.template.yaml`, `tts_bed_exit_ko.mp3` (`model_dir: "../models"`) |
| **시연 · 테스트용 MP4** | `run_data/` | 제외 | `test_bed.mp4` 등 |
| **통합 데이터셋** | `data/` *(또는 `datasets/`)* | 제외 | 파인튜닝·평가용 원본/라벨 데이터셋 |
| **파인튜닝 · 연구 실험 결과** | `research/results/` *(또는 `finetune/runs/`)* | 제외 | 학습 가중치, 벤치마크 지표 |
| **노드 단위 · 통합 검증 결과** | `results/<짧은-커밋>_vX_X/` | 제외 | `run_validation.sh` 산출물 |

---

## 3. 카메라 ROI 라벨링 (`config/` 저장)

```bash
ros2 run part5_bed_exit label_roi \
  --camera-index 2 \
  --output "$PWD/src/part5_bed_exit/config/cam_roi_test.json" \
  --force
```

---

## 4. Part 5 노드 구동 (비동기 한국어 여성 TTS 포함)

### 4.1 실제 웹캠 구동
```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$PWD/src/part5_bed_exit/config/cam_roi_test.json" \
  -p camera_index:=2 \
  -p capture_fps:=30.0 \
  -p show:=true
```

### 4.2 MP4 구동 (기존 v1.3 파라미터 호환)
```bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$PWD/src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json" \
  -p video_path:="$PWD/src/part5_bed_exit/run_data/test_bed.mp4" \
  -p capture_fps:=30.0 \
  -p output_topic:=/patient/bed_exit \
  -p loop_video:=false \
  -p show:=false
```

### 4.3 YAML 파라미터 파일 + 런치 구동
```bash
ros2 launch part5_bed_exit bed_exit.launch.py \
  params_file:="$PWD/src/part5_bed_exit/config/bed_exit.params.template.yaml" \
  method_config:="$PWD/src/part5_bed_exit/config/cam_roi_test.json" \
  camera_index:=2
```

---

## 5. TTS 단독 확인

```bash
# 내장 한국어 여성 음성 1회 재생
ffplay -nodisp -autoexit -loglevel quiet src/part5_bed_exit/config/tts_bed_exit_ko.mp3

# TtsSpeaker 비동기 반복 -> 정상 복귀 시 1회 완료 후 중단 확인
PYTHONPATH=src/part5_bed_exit python3 -c '
import time
from part5_bed_exit.tts_speaker import TtsSpeaker
s = TtsSpeaker(repeat_interval=1.0)
s.set_active(True)
time.sleep(5.0)
s.set_active(False)
while s.is_speaking:
    time.sleep(0.1)
s.close()
'
```

---

## 6. rosbag 녹화 · 재생 및 토픽 확인

### 6.1 토픽 실시간 확인
```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0
ros2 topic echo /patient/bed_exit std_msgs/msg/Bool
```

### 6.2 rosbag 녹화
```bash
mkdir -p src/part5_bed_exit/results/bags
ros2 bag record -o "src/part5_bed_exit/results/bags/run_$(date +%Y%m%d_%H%M%S)" /patient/bed_exit
```

### 6.3 rosbag 5초 지연 백그라운드 조용히 재생 + 토픽 확인
```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0

(sleep 5 && exec ros2 bag play "$PWD/실험_rosbag2_2026_11_20-17_32_24" \
  --loop --disable-keyboard-controls > /dev/null 2>&1) &
BAG_PID=$!

ros2 topic echo /patient/bed_exit std_msgs/msg/Bool
kill $BAG_PID 2>/dev/null || true
```

*(특정 시점부터 재생하려면 `ros2 bag play` 뒤에 `--start-offset 230` 추가)*

---

## 7. 단위 · 통합 테스트 검증

```bash
# v1.3 하위호환 단위 테스트 (51/51)
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit

# v1.4 신규 기능(TTS·파라미터화·앙상블) 단위 및 통합 테스트
bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh unit
bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh integration

# Part 3 + Part 5 연동 통합 테스트
bash part_integration/part3_part5/run.sh --video-path src/part5_bed_exit/run_data/test_bed.mp4
```
