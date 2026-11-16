# Part 5 네 방법 라벨·출력 형식

확인일: 2026-11-16. 현재 구현은 학습하지 않고 COCO 또는 MediaPipe 사전학습 모델을 추론에 사용한다. “학습 라벨”, “모델 추론 출력”, “Part 5 Bool 변환”을 구분한다.

## 1. 네 방법 비교

| 방법 | 사전학습 의미 | 구현이 읽는 추론 출력 | 단일 ROI 원판정 | 판단 불가 조건 예시 |
|---|---|---|---|---|
| Detection | COCO `person` class 0의 사람 box | `boxes.xyxy`, `boxes.conf` | 선택된 사람 box 중 ROI 밖 픽셀 면적 비율이 임계값 초과 | 사람 없음, 환자 대응 실패, 다중 후보 모호 |
| Segmentation | COCO `person` instance mask | 원본 크기의 `masks.data`와 사람 box | 선택된 사람 mask 중 ROI 밖 픽셀 비율이 임계값 초과 | mask 없음/빈 mask, 환자 대응 실패 |
| YOLO Pose | COCO person + 17개 관절 | `(N, 17, 3)`의 pixel `x, y, confidence`와 사람 box | 공통 13관절 중 신뢰 가능한 점 하나라도 ROI 밖이면 `True` | 몸통 점 부족, 누락 관절 때문에 안전을 확정할 수 없음 |
| MediaPipe Pose | BlazePose 계열 33개 landmark | pose별 33개 normalized landmark의 `x, y, z, visibility, presence` | `x,y`를 pixel로 바꾸고 `min(visibility,presence)`를 confidence로 사용한 뒤 공통 13관절 판정 | pose 없음, 몸통 점 부족, 환자 대응 실패, 안전 확정에 필요한 관절 누락 |

Ultralytics의 COCO pose 형식은 17개 관절과 `(x,y,visibility)` 라벨을 사용한다. Detection YOLO 라벨은 `class x_center y_center width height`, segmentation 라벨은 class 뒤에 polygon 좌표를 둔다. 추론 시 이 프로젝트는 라벨 파일을 읽는 것이 아니라 Ultralytics `Results`의 box/mask/keypoint tensor를 읽는다.

MediaPipe `PoseLandmarkerResult`는 이미지에서 검출한 pose별 landmark 목록을 반환한다. 현재 정적 POC는 `RunningMode.IMAGE`를 사용한다.

공식 근거:

- [Ultralytics COCO Pose 17-keypoint schema](https://docs.ultralytics.com/datasets/pose/coco/)
- [Ultralytics detection·segmentation·pose label formats](https://docs.ultralytics.com/guides/data-collection-and-annotation/)
- [Ultralytics instance segmentation result masks](https://docs.ultralytics.com/tasks/segment/)
- [MediaPipe Tasks Vision PoseLandmark/PoseLandmarkerResult](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision)
- [MediaPipe Python running modes](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/RunningMode)

## 2. YOLO Pose 17개 관절

| index | landmark | index | landmark |
|---:|---|---:|---|
| 0 | nose | 9 | left_wrist |
| 1 | left_eye | 10 | right_wrist |
| 2 | right_eye | 11 | left_hip |
| 3 | left_ear | 12 | right_hip |
| 4 | right_ear | 13 | left_knee |
| 5 | left_shoulder | 14 | right_knee |
| 6 | right_shoulder | 15 | left_ankle |
| 7 | left_elbow | 16 | right_ankle |
| 8 | right_elbow |  |  |

## 3. MediaPipe Pose 33개 landmark

| index | landmark | index | landmark | index | landmark |
|---:|---|---:|---|---:|---|
| 0 | nose | 11 | left_shoulder | 22 | right_thumb |
| 1 | left_eye_inner | 12 | right_shoulder | 23 | left_hip |
| 2 | left_eye | 13 | left_elbow | 24 | right_hip |
| 3 | left_eye_outer | 14 | right_elbow | 25 | left_knee |
| 4 | right_eye_inner | 15 | left_wrist | 26 | right_knee |
| 5 | right_eye | 16 | right_wrist | 27 | left_ankle |
| 6 | right_eye_outer | 17 | left_pinky | 28 | right_ankle |
| 7 | left_ear | 18 | right_pinky | 29 | left_heel |
| 8 | right_ear | 19 | left_index | 30 | right_heel |
| 9 | mouth_left | 20 | right_index | 31 | left_foot_index |
| 10 | mouth_right | 21 | left_thumb | 32 | right_foot_index |

## 4. 공통 비교용 13관절

| 공통 landmark | YOLO/COCO index | MediaPipe index |
|---|---:|---:|
| nose | 0 | 0 |
| left_shoulder | 5 | 11 |
| right_shoulder | 6 | 12 |
| left_elbow | 7 | 13 |
| right_elbow | 8 | 14 |
| left_wrist | 9 | 15 |
| right_wrist | 10 | 16 |
| left_hip | 11 | 23 |
| right_hip | 12 | 24 |
| left_knee | 13 | 25 |
| right_knee | 14 | 26 |
| left_ankle | 15 | 27 |
| right_ankle | 16 | 28 |

코드의 `COMMON_LANDMARKS`, `COCO_COMMON`, `MP_COMMON`이 이 표를 단일 원천으로 사용한다. YOLO에만 있는 눈·귀와 MediaPipe에만 있는 손가락·발꿈치·발끝은 공정 비교에서 제외한다.

## 5. Roboflow Bed Exit v4 원본 라벨

로컬 `data.yaml`:

```yaml
nc: 3
names: ['bed_exit', 'hospital_bed', 'in_bed']
```

각 `.txt` 행은 YOLO detection 형식이다.

```text
class_id x_center y_center width height
```

모든 좌표는 0~1 정규화 값이다. 848장 클래스 조합 분포:

| 클래스 조합 | 이미지 수 |
|---|---:|
| `bed_exit`만 | 213 |
| `bed_exit + hospital_bed` | 55 |
| `hospital_bed`만 | 73 |
| `hospital_bed + in_bed` | 51 |
| `in_bed`만 | 456 |

Roboflow의 class 0은 COCO `person` class 0과 이름만 같은 숫자일 뿐 의미가 다르다. 원본 라벨을 COCO 사전학습 출력과 직접 비교하거나 `classes=[0]` 필터의 person으로 해석하면 안 된다.

## 6. 확정 사용 정책

1. 원본 Roboflow 라벨은 변경하지 않고 `raw/`에 보존한다.
2. `bed_exit`/`in_bed`는 현재 POC의 표본 층화와 provisional expected Bool에만 사용한다.
3. 네 방법의 모델 입력·출력 클래스에는 `bed_exit`/`in_bed`를 사용하지 않는다. 네 방법 모두 먼저 사람 geometry를 추출한 뒤 같은 ROI 정책으로 Bool을 만든다.
4. `hospital_bed` box는 정적 POC에서만 이미지별 ROI로 사용한다. 배포 카메라 ROI를 대체하지 않는다.
5. 향후 Roboflow box를 학습용 person 영역으로 쓸 경우 원본을 덮어쓰지 않고 별도 derived dataset을 만든다. `bed_exit`와 `in_bed` box가 실제 사람 영역인지 20장 이상 시각 검수한 뒤 두 클래스를 `person`으로 병합한다.
6. semantic state label을 제거한 derived person dataset과 원본 state label의 성능을 같은 결과로 보고하지 않는다.
