# 통합시험 결과

모델 단독 정확도 결과가 아니라 입력 장치·프레임 처리·ROS 발행·기록 경계를 함께 실행한
결과다.

- `mp4_four_model_benchmark/full_v1/`: 한 MP4를 네 모델에 동시에 입력한 비교 영상·CSV
- `mp4_bypass_single_model/`: MP4를 카메라 대신 입력해 단일 모델 토픽을 기록한 rosbag
- `webcam/yolo_pose_webcam_v1/`: 실제 USB 웹캠과 YOLO Pose 토픽 기록

MP4는 시각적 결과, CSV는 프레임·방법별 수치 결과, MCAP·metadata.yaml은 ROS bag
기록이다.
