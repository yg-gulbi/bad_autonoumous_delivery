# Integration benchmark

## 목적

현재 4개 모델의 개별 성능과 4모델 동시 실행 성능을 같은 MP4·라벨로 비교한다.

## 입력

- MP4: 640×480, 30 FPS, 1,024프레임
- 라벨: SAFE 353, EXIT 671, EXIT 이벤트 9개
- 모델: Detection, Segmentation, YOLO Pose, MediaPipe Pose

## 출력

- `frames.csv`: 프레임별 정답·원판정·5프레임 투표·지연·오류
- `comparison.csv`: Recall·SAFE FPR·Precision·F1·Coverage·이벤트·P95·FPS
- `comparison.mp4`: 판정 근거 시각화
- `rosbag/`: 모델별 토픽과 최종 `/patient/bed_exit`
- `run.json`: 실행 조건·경로·요약

## 실행 구분

- `--mode individual --method <name>`: 모델 하나만 계측
- `--mode all`: 4모델을 같은 프레임에서 연속 실행

## 실제 출력 — 64e57ce

| 실행 | 모델 | Recall | SAFE FPR | F1 | Coverage | P95 ms | FPS |
|---|---|---:|---:|---:|---:|---:|---:|
| 개별 | Detection | 90.31% | 50.15% | 83.64% | 96.00% | 78.33 | 15.12 |
| 개별 | Segmentation | 83.16% | 13.58% | 87.46% | 96.68% | 93.56 | 10.71 |
| 개별 | YOLO Pose | 91.51% | 43.59% | 85.40% | 99.80% | 112.23 | 11.35 |
| 개별 | MediaPipe Pose | 97.17% | 65.16% | 83.97% | 100.00% | 36.14 | 28.71 |
| 전체 | 4모델 파이프라인 | 모델별 동일 | 모델별 동일 | 모델별 동일 | 모델별 동일 | 57.58~173.90 | 3.03 |

전체 rosbag은 모델별 4,019개와 최종 YOLO Pose 1,022개, 총 5,041개를 기록했다.

Pose 패널의 내부 영상은 원본 detector 근거를 표시한다. 추적 후 Pose 손실이 발생하면
내부에는 `UNAVAILABLE`이 보여도 상단 헤더·CSV·ROS 출력은 wrapper가 변환한 `EXIT`이다.


