# 64e57ce integration benchmark 결과

## 개별 모듈

| 모델 | Recall | SAFE FPR | Precision | F1 | Coverage | P95 ms | FPS | 이벤트 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Detection | 90.31% | 50.15% | 77.89% | 83.64% | 96.00% | 78.33 | 15.12 | 9/9 |
| Segmentation | 83.16% | 13.58% | 92.23% | 87.46% | 96.68% | 93.56 | 10.71 | 9/9 |
| YOLO Pose | 91.51% | 43.59% | 80.05% | 85.40% | 99.80% | 112.23 | 11.35 | 9/9 |
| MediaPipe Pose | 97.17% | 65.16% | 73.92% | 83.97% | 100.00% | 36.14 | 28.71 | 9/9 |

## 전체 모듈

- 정확도·Coverage·이벤트 결과: 개별 실행과 동일
- 전체 처리율: 3.03 FPS
- 모델 P95: Detection 168.52ms, Segmentation 173.90ms, YOLO Pose 138.94ms,
  MediaPipe Pose 57.58ms
- rosbag: 모델별 토픽 4,019개 + 최종 YOLO Pose 1,022개 = 5,041개

세부 값은 `summary.csv`, 각 실행의 `comparison.csv`, `frames.csv`, `run.json`,
`comparison.mp4`, `rosbag/`에 있다.

Notion 기존 수치와의 차이는 `baseline_delta.csv`에 있다. 기존 실행 환경 정보가 완전히
같지 않으므로 차이를 리팩터링 코드만의 영향으로 단정하지 않는다.

Pose 비교 영상은 내부 detector 근거와 상단 최종 wrapper 출력을 함께 표시한다.
추적 후 Pose 손실에서는 내부 `UNAVAILABLE`, 최종 헤더·CSV·ROS `EXIT`가 정상이다.


