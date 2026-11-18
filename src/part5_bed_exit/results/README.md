# Part 5 시험·실험 결과

이 디렉터리는 모델 시험과 ROS 통합시험 결과만 보관한다. 시험 입력은 `test_images/`,
모델 가중치는 `models/`에 두며 결과와 섞지 않는다.

## `model_tests/`: 모든 모델 단독 시험 결과

모델을 데이터셋·정적 이미지에 각각 실행한 결과는 전부 이 폴더 아래에 둔다.

### `model_tests/dataset_benchmarks/`: 데이터셋별 모델 실행 기록

- `roboflow_bed_exit_106_images/`: Roboflow 106장, 운영 4모델과 ViTPose·MAE 비교
- `roboflow_pose_20_images/`: Roboflow 20장, YOLO Pose·MediaPipe Pose 비교
- `slp_cover2_45_images/`: SLP Cover2 45장, Detection·Segmentation·Pose 비교
- `slp_pose_pair_2_images/`: SLP 비가림·가림 2장 Pose 비교
- `coco_in_bed_7_images/`: COCO 병상 장면 7장 관절·판정 평가
- `blanket_conditions_62_samples/`: 실제 담요 45장, 합성 15장, 복원 2장 비교

### `model_tests/exploratory_prototypes/`: 초기 모델 POC

- `slp_two_image_prototypes/`: SLP 정적 이미지 2장으로 수행한 Detection,
  Segmentation, YOLO Pose, ViTPose, AOT-GAN, MAE 초기 실험

### `model_tests/archived_runs/`: 대체된 과거 모델 시험

- `blanket_exit_methods_v1/`: 담요 조건별 초기 모델 비교
- `pose_mediapipe_roboflow20_v1~v3/`: Roboflow 20장 MediaPipe 실험 이력
- `pose_pair_slp_public_demo_v1~v2/`: SLP 2장 Pose 비교 이력

## `integration_tests/`: MP4·웹캠·ROS 통합시험 결과

- `mp4_four_model_benchmark/full_v1/`: 동일 MP4 1,024프레임의 4모델 비교·토픽·rosbag
- `mp4_bypass_single_model/segmentation_bag/`: MP4 우회 Segmentation 토픽 기록
- `mp4_bypass_single_model/yolo_pose_bag/`: MP4 우회 YOLO Pose 토픽 기록
- `webcam/yolo_pose_webcam_v1/`: 실제 웹캠 YOLO Pose 토픽 기록

## `archive/`: 구형 MP4 결과와 스모크 테스트

- `legacy_mp4_comparisons/`: rosbag 통합 전 MP4 비교
- `smoke_tests/`: 짧은 프레임으로 실행 여부만 확인한 결과

`archive/`는 삭제하지 않았다. 재검토가 끝난 뒤 별도 승인으로 정리한다.

## 경로 주의

일부 `run.json`과 `summary.json`의 절대경로는 실험 실행 당시 위치를 기록한 역사적
메타데이터다. 현재 파일 위치와 다를 수 있으며 원본 실행 기록 보존을 위해 자동으로
덮어쓰지 않는다.
