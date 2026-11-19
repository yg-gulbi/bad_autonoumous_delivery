웹캠으로 구동하는 end-to-end 모듈

# 1. ROS 2 환경 및 패키지 빌드
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash

# 2. ROI 설정 파일 준비 (파일 내 roi 수정 및 roi_reviewed: true 설정 필요)
cp src/part5_bed_exit/examples/methods.template.json \
  src/part5_bed_exit/examples/methods.local.json

# 3. 노드 실행
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=segmentation \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json"
  
====================================
 

# 코드 뷰 대비
돌리고

| 검증 범위 | 상태 | 현재 증거 |
|---|---|---|
| 모델 → ROI → 3/5 판정 | 완료 | 자동 테스트 |
| 카메라 오류 → 이력 초기화·미발행 | 완료 | 통합 테스트 |
| Bool 생성 → publisher 호출 | 완료 | 가짜 ROS publisher 테스트 |
| MP4 → 네 모델 → 결과표 | 완료 | 비교 실행기·CSV·영상 |
| 실제 USB 웹캠 입력 | 증적 미확보 | 현장에서 실행·화면 녹화 필요 |
| 실제 `/patient/bed_exit` ROS 발행 | 증적 미확보 | rosbag 필요 |
| 타 파트가 토픽을 실제 수신 | 미진행 | 타 파트 노드와 별도 검증 필요 |

Part 5에는 단위·통합 테스트가 있으며 최근 실행 결과는 `96 passed`입니다.


## 코드리뷰 대표 함수

대표 함수는 [BedExitNode.process_frame()](/home/hyunwoong/workspaces/bad_autonoumous_delivery/src/part5_bed_exit/part5_bed_exit/bed_exit_node.py:112)이 적절합니다.

이 함수 하나로 다음 흐름을 설명할 수 있습니다.

1. `capture.read()`로 USB 프레임 수신
2. `detector.judge(frame)` 호출
3. `BedExitState.update()`로 5프레임 3회 판정
4. 오류 시 이력 초기화·토픽 미발행
5. 유효 판정이면 `/patient/bed_exit` Bool 발행

핵심 알고리즘만 질문받으면 [BedExitState.update()](/home/hyunwoong/workspaces/bad_autonoumous_delivery/src/part5_bed_exit/part5_bed_exit/bed_exit_state.py:14)를 설명하면 됩니다.

## 플로우차트와 코드 대응

- USB 웹캠: `bed_exit_node.py`
- YOLO/Segmentation: `methods/bed_exit_core.py`
- 환자 추적: `PatientSelector`
- Bed ROI 판정: `SegmentationROI.decide()`
- 5프레임 버퍼: `bed_exit_state.py`
- 토픽 발행: `process_frame()`
- 모델 교체: `bed_exit_method.py`, `pretrained.py`
- MP4 테스트벤치: `research/evaluation/video_comparison.py`

다만 기존 플로우차트의 수치와 현재 코드에는 차이가 있습니다.

- 플로우차트: `imgsz=480`, `conf=0.35`
- 현재 기본값: `imgsz=640`, `conf=0.25`
- 플로우차트: ROI 외부 비율 `0.10`
- 현재 기본값: `0.05`

새 그림을 만들 필요는 없지만, 발표에서는 현재 코드값을 기준으로 설명해야 합니다.






## 실제 기능 테스트 증거 생성



================
webcam 통합 기능테스트

### 1. USB 입력 실행
이 화면을 녹화하면 “USB 입력 → ROI 표시 → 판정 상태”의 입력 방향 증거가 됩니다.

cd ~/workspaces/bad_autonoumous_delivery

cp src/part5_bed_exit/examples/methods.template.json \
   src/part5_bed_exit/examples/methods.webcam.json
   
>>>"roi_reviewed": true

source /opt/ros/jazzy/setup.bash
source /home/hyunwoong/venvs/rokey_venv/bin/activate
source install/setup.bash

unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0

ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=yolo_pose \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.webcam.json" \
  -p camera_index:=2 \
  -p capture_fps:=30.0 \
  -p show:=true


사용가능한 method:
yolo_pose
segmentation
mediapipe_pose
detection

욜로포즈는 관절하나라도 없으면 언디텍트로 구분해서, 계속 붉은색이 뜰것.


### 2. 출력 토픽 rosbag 기록

다른 터미널에서 실행합니다.

다른 터미널에서:

source /opt/ros/jazzy/setup.bash
source install/setup.bash

unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0

ros2 bag record \
  -o src/part5_bed_exit/results/integration/webcam/yolo_pose_usb_webcam_v1 \
  --topics /patient/bed_exit


다른터미널에서:
```bash
ros2 topic echo /patient/bed_exit
```

### 3. bag 결과 확인

```bash
ros2 bag info src/part5_bed_exit/results/integration/webcam/yolo_pose_usb_webcam_v1
```bash
ros2 bag play src/part5_bed_exit/results/integration/webcam/yolo_pose_usb_webcam_v1
```


===================
mp4 우회

모든 터미널:
cd ~/workspaces/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash

unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0

###1. 
cd ~/workspaces/bad_autonoumous_delivery

source /opt/ros/jazzy/setup.bash
source .venv/bin/activate

export PYTHONPATH="$PWD/src/part5_bed_exit:${PYTHONPATH}"

python src/part5_bed_exit/research/integration/mp4_ros_replay.py \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --method yolo_pose \
  --topic /patient/bed_exit \
  --show
 ###2. 
 
 source /opt/ros/jazzy/setup.bash

ros2 bag record \
  -o part5_bed_exit_mp4_bag_test \
  /patient/bed_exit
  
 ###3.
source /opt/ros/jazzy/setup.bash
ros2 topic echo /patient/bed_exit


###나중에
 ros2 bag play part5_bed_exit_mp4_bag_02/
 ros2 topic echo /patient/bed_exit


##################기능테스트 완료##################

## pose 폴리시 설정:

{
  "method_settings": {
    "policy": "any_body",
    "point_conf": 0.5,
    "tolerance_px": 3.0
  }
}


# 벤치마킹 테스트 

##webcam에서 4개 메서드만 비교 실험

cd /home/hyunwoong/workspaces/bad_autonoumous_delivery

env MPLCONFIGDIR=/tmp/part5-mpl \
PYTHONPATH=src/part5_bed_exit \
.venv/bin/python -m research.evaluation.video_comparison \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --output-dir src/part5_bed_exit/results/test_input_sequence1_four_methods_full_v2 \
  --methods detection segmentation yolo_pose mediapipe_pose \
  --panel-width 320
  
##mp4 우회에서 통합 벤치마크 수행

cd ~/workspaces/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash

unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0


-각 메서드를 써서 내보내는 토픽이 다르기 때문에 각각을 래핑
-얘가 알아서 배그
-화면에서 나오는 정책
Pose 정책:
- 최초 사람 미검출: 판단 불가, 미발행
- 정상 추적 후 사람·관절 소실: 이탈 후보 True
- ambiguous_people: 판단 불가 유지
- 최종 출력: 동일하게 최근 5프레임 중 True 3회 이상
기존 판단 불가 정책으로 비교하려면 다음 옵션을 추가합니다.
--no-pose-loss-after-trackPose 정책:
- 최초 사람 미검출: 판단 불가, 미발행
- 정상 추적 후 사람·관절 소실: 이탈 후보 True
- ambiguous_people: 판단 불가 유지
- 최종 출력: 동일하게 최근 5프레임 중 True 3회 이상
기존 판단 불가 정책으로 비교하려면 다음 옵션을 추가합니다.
--no-pose-loss-after-track

검증 결과:
- 단위·통합 테스트: 13 passed
- 실제 2프레임 ROS 스모크 테스트: 완료
- 5개 토픽 recorder 구독: 성공
- rosbag: 5개 메시지 기록
- comparison.csv, frames.csv, comparison.mp4, run.json: 생성 확인


cd ~/workspaces/bad_autonoumous_delivery

source /opt/ros/jazzy/setup.bash
source .venv/bin/activate

unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0
export PYTHONPATH="$PWD/src/part5_bed_exit:${PYTHONPATH}"

env MPLCONFIGDIR=/tmp/part5-mpl \
  python -m research.integration.four_method_mp4_benchmark \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --output-dir src/part5_bed_exit/results/four_method_mp4_bag_full_v1 \
  --methods detection segmentation yolo_pose mediapipe_pose \
  --final-method yolo_pose \
  --panel-width 320 \
  --show
  

 
# 
정확한 코드 이해
유닛테스트


