# v3 integration benchmark 결과

`load_bed_exit_method()`를 통해 생성한 개별 4모델과 전체 4모델을 동일한
1,024프레임 MP4와 라벨로 실행한 결과다.

## 개별 모델 결과

| 모델 | Recall | SAFE FPR | Precision | F1 | Coverage | P95 ms | FPS | 이벤트 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Detection | 90.31% | 50.15% | 77.89% | 83.64% | 96.00% | 71.10 | 15.56 | 9/9 |
| Segmentation | 83.16% | 13.58% | 92.23% | 87.46% | 96.68% | 109.45 | 11.06 | 9/9 |
| YOLO Pose | 91.51% | 43.59% | 80.05% | 85.40% | 99.80% | 125.66 | 11.25 | 9/9 |
| MediaPipe Pose | 97.17% | 65.16% | 73.92% | 83.97% | 100.00% | 38.25 | 27.96 | 9/9 |

## 4모델 전체 파이프라인

- 전체 처리율: 3.38 FPS
- 모든 모델의 정확도·Coverage·이벤트 결과는 개별 실행과 동일
- 모델별 P95: Detection 151.86ms, Segmentation 149.48ms,
  YOLO Pose 122.83ms, MediaPipe Pose 54.38ms
- rosbag: 모델별 토픽 4,019개와 최종 YOLO Pose 1,022개, 총 5,041개
- 모든 실행: `status=complete`, 1,024프레임
- 총 산출물 크기: 약 51MB

## 산출물

각 실행 디렉터리에는 `frames.csv`, `comparison.csv`, `comparison.mp4`, `run.json`,
`rosbag/`이 있다. 전체 산출물 검증 테스트도 통과했다.
