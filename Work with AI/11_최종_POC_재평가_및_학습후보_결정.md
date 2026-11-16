# 11. 최종 POC 재평가 및 학습 후보 결정

기준일: 2026-11-16  
대상: Part 5 병상 이탈 감지, Roboflow 고정 20장, YOLO Pose·MediaPipe Pose

## 1. 결론

- 수동 검수 결과 20장 중 8장은 실제 빈 병상이다. 두 모델의 `no_person`은 이 8장에서는 실패가 아니라 사람 없음 정답이다.
- 배포 코드에서는 `no_person`을 정상 Bool `False`로 바꾸지 않는다. 사람이 사라진 상황과 빈 병상을 한 장만 보고 구분할 수 없으므로 안전 계약상 `JudgmentUnavailable`과 이력 초기화를 유지한다.
- 사람 유무 인식은 YOLO Pose 19/20(95%), MediaPipe Pose 17/20(85%)다.
- 빈 병상 정답과 환자 장면의 올바른 Bool을 함께 요구한 정적 장면 통과율은 YOLO Pose 14/20(70%), MediaPipe Pose 10/20(50%)다.
- 1~2일 학습 후보는 `YOLO Detection`과 `YOLO Pose` 두 개로 줄인다. Segmentation은 mask 정답이 없고, MediaPipe Pose Landmarker는 공식 커스텀 재학습 경로가 없어 이번 학습 대상에서 제외한다.
- 원본을 보존하는 Detection/Pose 전처리 스크립트를 추가하고 실제 848장/312장 변환을 검증했다.

## 2. 20장 수동 검수 및 성능 재평가

### 판정 기준

1. 빈 병상 8장: 모델이 사람 후보를 만들지 않고 `no_person`이면 사람 유무 인식 정답.
2. 환자 있음 12장: 사람 후보 존재 여부와 이탈 Bool 생성 성공 여부를 분리.
3. `incomplete_body_coverage`, `patient_unassociated`, `ambiguous_people`는 사람 후보를 검출했더라도 최종 Bool을 만들지 못한 것으로 집계.
4. #2는 Roboflow 원본 클래스가 `bed_exit`지만 수동 단일 ROI 규칙에서는 정상.
5. 정적 장면 통과율은 `빈 병상 no_person 정답 + 환자 장면 올바른 Bool`.

### 이미지별 결과

| # | 장면 수동 확인 | YOLO Pose | MediaPipe Pose | 평가 |
|---:|---|---|---|---|
| 1 | 환자 있음, 이탈 | True | incomplete | YOLO 통과 |
| 2 | 환자 있음, ROI 내부 정상/조기징후 | incomplete | no_person | 모두 실패 |
| 3 | 환자 있음, 이탈 | True | patient unassociated | YOLO 통과 |
| 4 | 환자 있음, 이탈 | True | ambiguous people | YOLO 통과 |
| 5 | 환자 있음, 이탈 | True | True | 모두 통과 |
| 6 | 환자 있음, 이탈 | True | incomplete | YOLO 통과 |
| 7 | 환자 있음, 정상 | incomplete | no_person | 모두 실패 |
| 8 | 환자 있음, 정상 | incomplete | no_person | 모두 실패 |
| 9 | 환자 있음, 정상 | incomplete | incomplete | 모두 실패 |
| 10 | 환자 있음, 정상 | no_person | incomplete | 모두 실패 |
| 11 | 환자 있음, 정상 | incomplete | incomplete | 모두 실패 |
| 12 | 환자 있음, 정상 | False | False | 모두 통과 |
| 13 | 빈 병상 | no_person | no_person | 모두 통과 |
| 14 | 빈 병상 | no_person | no_person | 모두 통과 |
| 15 | 빈 병상 | no_person | no_person | 모두 통과 |
| 16 | 빈 병상 | no_person | no_person | 모두 통과 |
| 17 | 빈 병상 | no_person | no_person | 모두 통과 |
| 18 | 빈 병상 | no_person | no_person | 모두 통과 |
| 19 | 빈 병상 | no_person | no_person | 모두 통과 |
| 20 | 빈 병상 | no_person | no_person | 모두 통과, Bed box annotation outlier |

### 재계산 수치

| 지표 | YOLO Pose | MediaPipe Pose | 의미 |
|---|---:|---:|---|
| 빈 병상 `no_person` | 8/8, 100% | 8/8, 100% | 빈 병상에서 사람을 만들지 않음 |
| 환자 장면 사람 후보 | 11/12, 91.7% | 9/12, 75.0% | 최종 Bool과 무관한 presence |
| 전체 사람 유무 인식 | 19/20, 95% | 17/20, 85% | occupied/empty 분류 관점 |
| 환자 장면 유효 Bool | 6/12, 50% | 2/12, 16.7% | 이탈 규칙까지 실행 가능한 비율 |
| 유효 Bool 조건부 일치 | 6/6, 100% | 2/2, 100% | 성공 사례만 남긴 편향된 수치 |
| 정적 장면 통과율 | 14/20, 70% | 10/20, 50% | 빈 병상 정답 또는 올바른 Bool |
| CPU 평균 추론 | 55.162 ms | 29.540 ms | 로컬 CPU, 모델 로드 제외 |

20장은 한 촬영 시퀀스 계열이고 임상 정답이 아니다. 위 수치는 현재 파이프라인의 진단용 수치이며 일반화 정확도로 보고하지 않는다.

## 3. 두 조건 중 하나를 충족하는 확보 가능 데이터

조건 A는 병상 관절 정답, 조건 B는 담요 가림이다.

| 데이터 | 충족 조건 | 확보 상태 | 학습/평가 역할 | 제한 |
|---|---|---|---|---|
| Mannequin RGB In-Bed Pose | A | RGB 312장+3×16 LSP 관절 확보 | 누운 병상 YOLO Pose 파인튜닝 | 마네킹, 세로 시점, 담요 없음 |
| SLP MICCAI19 공개 부분 | B | `cover2` RGB 45장 확보 | 담요 조건 검출률/정성 검토 | 관절 MAT 암호화, 한 피험자 연속 장면 |
| BlanketSet | B | 405행 조건표와 문서만 확보 | 조건 설계 | 영상 법적 접근 제한, 정답 관절 없음 |
| BlanketGen2-Fit3D | B | 38 MB 보충자료 확보, 29.8 GB 영상 선택 다운로드 | 합성 담요 증강 | 원본 Fit3D RGB/관절은 별도 |
| FallVision | A의 보조 | 다운로드 경로 준비 | 시간축 파이프라인 smoke test | Pose CSV는 YOLOv7 의사 라벨, 정답 아님 |

다운로드 스크립트는 `src/part5_bed_exit/research/datasets/download_public_pose_blanket.py`다.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/datasets/download_public_pose_blanket.py --list

# --dataset 값: mannequin_rgb_in_bed, slp_miccai19_public,
# blanketset_metadata, blanketgen2_fit3d, fallvision_pose_preview
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/datasets/download_public_pose_blanket.py \
  --dataset mannequin_rgb_in_bed
```

BlanketGen2 전체 29.8 GB는 `--dataset blanketgen2_fit3d --mode full`로만 받는다.

## 4. 네 모델의 1~2일 학습 가능성

| 후보 | 현재 학습 정답 | 1~2일 실행 가능성 | 결정 |
|---|---|---|---|
| YOLO Detection | Roboflow 848장, person으로 합칠 box 775개, bed box 181개 | 높음 | 1순위 학습 |
| YOLO Pose | Mannequin 312장, 정확히 대응되는 사지 12관절 | 중간 | 2순위 제한 실험 |
| YOLO Segmentation | mask 정답 없음 | 낮음 | 이번 학습 제외 |
| MediaPipe Pose | 공식 Pose Landmarker 커스텀 학습 경로 없음 | 매우 낮음 | 추론 baseline만 유지 |

Segmentation에서 box를 사각형 mask로 바꾸거나 SAM pseudo-mask를 정답으로 사용하면 이불과 환자 경계를 보장할 수 없다. MediaPipe 공식 API는 Lite/Full/Heavy 추론을 제공하지만 Pose Landmarker 파인튜닝 튜토리얼은 제공하지 않으며 Model Maker도 더 이상 적극 유지되지 않는다.

## 5. YOLO Pose 구조와 학습 원리

현재 환경은 `ultralytics 8.4.210`, `yolo11n-pose.pt`다.

```text
RGB 입력
  → resize/letterbox와 augmentation
  → YOLO11 Backbone(Conv, C3k2, SPPF, C2PSA)
  → 다중 해상도 특징 결합(P3/8, P4/16, P5/32)
  → Pose Head
      ├─ person class
      ├─ bounding box
      └─ 17 × (x, y, visibility/confidence) keypoints
  → box/instance 선택
  → keypoint decode
  → 공통 관절 confidence 필터
  → Bed ROI 내부/외부 판정
  → 프레임 Bool
  → 최근 유효 5프레임 중 True 3회 투표
```

YOLO Pose는 먼저 사람을 검출한 뒤 별도 모델로 관절을 붙이는 구조가 아니라 P3/P4/P5 특징에서 box·class·keypoint를 함께 예측하는 `Pose(Detect)` head를 사용한다. 로컬 구현에서 keypoint branch는 각 feature level에 두 개의 3×3 convolution과 1×1 convolution을 두고 17×3 값을 출력한다.

학습 시 `box_loss`, `cls_loss`, `dfl_loss`, 객체 면적과 OKS sigma로 정규화한 `pose_loss`, 관절 visibility의 `kobj_loss`를 함께 최적화한다. 담요 아래 관절을 개선하려면 confidence만 바꾸는 것이 아니라 담요·누운 자세 정답 keypoint와 visibility가 학습 데이터에 포함돼야 한다.

## 6. 학습 규모와 도메인 차이

| 축 | 사전학습 COCO | 목표 데이터 |
|---|---|---|
| 자세 | 서기·걷기 중심 | 누움·상체 기상·가장자리 앉기 |
| 시점 | 일반 수평 시점 | 침대 발치/사선 고정 카메라 |
| 가림 | 일상 객체 가림 | 담요가 몸 윤곽과 사지를 장시간 가림 |
| 배경 | 다양한 야외·실내 | 침대·난간·모니터가 반복되는 병실 |
| 사람 크기 | 다양한 크기 | 세로 화면에서 좁고 길게 배치 |
| 현재 Pose 보충 | 실제 사람 COCO | 마네킹 312장, 담요 없음 |

현재 데이터만으로 병상 도메인 적응 POC는 가능하지만 환자·병실·담요 일반화 주장은 불가능하다. 연속 프레임을 무작위 분할하면 거의 같은 장면이 train/test에 섞여 성능이 부풀려진다.

### 1~2일 실험 규모

1. Detection: YOLO11n과 YOLO11s를 각각 50~100 epoch, `imgsz=640`, early stopping으로 비교.
2. Pose: YOLO11n-pose 한 모델만 50~100 epoch. 312장을 250/31/31로 임시 분할하지만 환자 단위 일반화 수치로 사용하지 않음.
3. SLP 45장은 학습에 섞지 않고 담요 holdout 진단에 사용. 관절 정량평가가 필요하면 공개 이미지의 보이는 사지 12개를 별도 수동 라벨링.
4. 자체 촬영은 위험한 실제 추락 없이 상체 기상·가장자리 앉기·팔다리 이탈·빈 병상·담요 가림을 촬영하고 사람/세션 단위로 분리.

## 7. 확정한 전처리

스크립트: `src/part5_bed_exit/research/datasets/prepare_training_data.py`

### Roboflow → Detection

- `bed_exit`와 `in_bed` box를 `person`으로 병합.
- `hospital_bed` box는 별도 class로 보존.
- 상태 class 예측 대신 사람 geometry와 고정 Bed ROI를 분리.
- 결과: 848장, person 775 boxes, hospital_bed 181 boxes.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/datasets/prepare_training_data.py \
  --task roboflow-detection \
  --source src/part5_bed_exit/research/data/datasets/roboflow_bed_exit_v4/raw \
  --output src/part5_bed_exit/research/data/training/roboflow_person_bed_detect_v1
```

### Mannequin LSP → YOLO Pose COCO17 호환

- LSP의 양쪽 어깨·팔꿈치·손목·골반·무릎·발목 12개만 COCO 인덱스에 직접 매핑.
- LSP neck/head top은 COCO nose/eye/ear와 같은 점이 아니므로 변환하지 않음.
- COCO 얼굴 5점은 `(0, 0, visibility=0)`.
- LSP invisible은 YOLO visibility 1, visible은 2.
- box는 매핑된 사지 관절 외곽에 10% padding을 주어 생성.
- 결과: COCO17 형식 312장, train 250/val 31/test 31.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/datasets/prepare_training_data.py \
  --task mannequin-pose \
  --source src/part5_bed_exit/research/data/datasets/mannequin_rgb_in_bed/extracted/colorManneNumbered \
  --output src/part5_bed_exit/research/data/training/mannequin_coco17_pose_v1
```

원본 데이터는 변경하지 않는다. 출력 폴더가 비어 있지 않으면 기존 결과를 덮어쓰지 않고 중단한다.

## 8. 다음 실행 순서

1. Detection 전처리 산출물의 box 30장을 수동 검수.
2. YOLO11n/s Detection 학습 후 빈 병상·환자 있음·ROI 경계 평가.
3. Pose 변환 관절 30장을 시각화 검수한 뒤 YOLO11n-pose 한 번 파인튜닝.
4. pretrained와 fine-tuned를 같은 Roboflow 20장, SLP 45장에 비교.
5. 환자 장면 유효 Bool 비율, 빈 병상 오검출, 담요 tracking lost, 지연으로 최종 모델 선정.

## 9. 근거 자료

- Roboflow Bed Exit v4: https://universe.roboflow.com/helppai/bed-exit/dataset/4
- SLP: https://github.com/ostadabbas/SLP-Dataset-and-Code
- BlanketSet: https://rdm.inesctec.pt/dataset/nis-2022-004
- BlanketGen2-Fit3D: https://doi.org/10.25747/44jy-3v61
- FallVision: https://doi.org/10.7910/DVN/75QPKK
- Ultralytics Pose task: https://docs.ultralytics.com/tasks/pose/
- Ultralytics Pose dataset format: https://docs.ultralytics.com/datasets/pose/
- MediaPipe Pose Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker
- MediaPipe Model Maker: https://developers.google.com/edge/mediapipe/solutions/model_maker
