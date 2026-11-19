# `part6_obstacle_classifier` 테스트 및 검증 진행과정 보고서

## 1. 파이프라인 단위 테스트 (`pytest`)
- **테스트 파일**: `test/test_pipeline.py`
- **실행 명령어**:
  ```bash
  PYTHONPATH=/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier pytest test/test_pipeline.py
  ```
- **결과**: **13 passed** (5개 테스트 함수, 총 13개 파라미터 케이스 모두 통과)

| 테스트 함수 | 케이스 수 | 검증 내용 |
|---|---|---|
| `test_valid_results_preserve_header` | 2 | 탐지 0건(`obstacle_present: False`) 및 1건(`True`) 시 `header`와 `detections` 유지 |
| `test_invalid_image_is_rejected` | 4 | `None`, 빈 배열, 2D 이미지, 4채널 이미지 입력 시 `ValueError` 발생 |
| `test_invalid_model_result_is_rejected` | 5 | `NaN`/범위 초과 신뢰도, 빈 라벨, 이미지 경계 밖 `bbox` 입력 시 `ValueError` 발생 |
| `test_unconfigured_model_does_not_report_clear` | 1 | 모델 미설정 시 `RuntimeError` 발생 및 `build_error`의 `obstacle_present: None` 유지 |
| `test_valid_depth_includes_pose_and_invalid_depth_omits_pose` | 1 | 유효 Depth(`2.0m`) 객체는 `pose` 포함, 무효 Depth(`0`) 객체는 `pose` 제외 |

---

## 2. 전처리 테스트 (다운샘플링 vs ROI 크롭 비교)
- **도구**: `part6_obstacle_classifier/test_image_downsample.py`
- **대상**: `images_100` (`1280x720`, 100장), 모델: 기본 `yolo11n.pt`

| 실험 단계 | 입력 처리 | 관찰 결과 |
|---|---|---|
| **① 다운샘플링만 적용** | `1280x720` → `512x288` (`INTER_AREA`) | 배경 벽면 `tv`, 왼쪽 파티션(`refrigerator` 오인식)이 매 프레임 탐지되고, 멀리 있는 인형은 대부분 미탐지 |
| **② 복도 ROI 크롭 + 다운샘플링** | `crop-ratio 0.15,0.42,0.95,1.0` → `512x288` | 벽면 `tv`·파티션 오탐 **0건**으로 제거, 멀리 있던 인형(`teddy bear 0.26~0.52`) 탐지 개선 |

---

## 3. 커스텀 `medical_box` 모델 학습 및 오탐 개선 테스트
- **원본 영상**: `/home/mu-02/rokey_ws/part6_assets/robot2_video_114934.mp4` (`512x288`, 15fps, 2,602프레임 → 중복 제거 220장)
- **상세 학습 로그**: `/home/mu-02/rokey_ws/part6_assets/medical_box_dataset/TRAINING_LOG.md`

| 단계 | 학습 데이터 구성 | `val` 성능 (`mAP50 / mAP50-95`) | `images_100` 오탐 (100장) | 결과 폴더 (`test_masked_results/`) |
|---|---|---|---|---|
| **1차 (`v1`)** | 박스 프레임만 (`175 / 45`) | `0.995 / 0.858` (`45장`) | **81 / 100** (빈 회색 바닥 오탐) | `part6_assets/test_masked_results/v1_images100_no_box_fp81/` |
| **2차 (`v2`)** | 박스 + 네거티브 배경(`80/20`) + 2x2 모자이크(`60/15`) + `mosaic=1.0` | **`0.995 / 0.892`** (`80장`) | **0 / 100** (오탐 완전 제거) | `part6_assets/test_masked_results/v2_val_box45_bg20_mosaic15/` |

---

## 4. 실기 TurtleBot4 연동 테스트 방법

### ① 실시간 RGB(YOLO 마스킹) + Depth 카메라 화면 확인
```bash
cd /home/mu-02/rokey_ws/bad_autonoumous_delivery
colcon build --packages-select part6_obstacle_classifier
source install/setup.bash

ros2 run part6_obstacle_classifier test_turtlebot_viewer \
  --ros-args -p model_path:=/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier/models/medical_box_yolo11n.pt
```

### ② 본 노드(이벤트 기반) + `map` 좌표 검증
```bash
# 터미널 1: 노드 실행 (/robot2 예시)
ros2 run part6_obstacle_classifier obstacle_classifier \
  --ros-args -r __ns:=/robot2 \
  -p model_path:=/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier/models/medical_box_yolo11n.pt

# 터미널 2: 결과 JSON 확인
ros2 topic echo /robot2/obstacle_classifier/result

# 터미널 3: 검사 이벤트 1회 전송
ros2 topic pub --once /robot2/obstacle/inspect_image std_msgs/msg/Empty "{}"
```
