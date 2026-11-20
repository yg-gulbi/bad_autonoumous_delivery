# PART 05 Segmentation 베이스라인 vs 파인튜닝 실험 설계서

- **작성일**: 2026-11-20
- **목적**: 병합된 운영 코드(`part5-v1.3.4` + `pretrained.py` `"weights"` 선택 지원)를 유지한 상태에서, **사전학습 Segmentation 베이스라인 모델들**과 **`run_data/test_bed.mp4` 기반 파인튜닝 모델들**을 동일한 Part 5 판정 파이프라인(`SegmentationROI` + `BedExitState` 5프레임 중 3회 다수결)으로 공정 비교해 최적의 운영 `.pt` 가중치를 선정한다.

---

## 1. 핵심 문제 정의 및 가설

### 관찰된 도메인 간극
1. **시점·자세 방향 불일치**
   - 기준 영상([test_bed.mp4](../run_data/test_bed.mp4), 1,024프레임): 머리 아래·발 위(**세로 180° 반전** 사다리꼴 ROI)
   - 실제 시연 웹캠([cam_roi.json](../config/cam_roi.json), `camera_index: 2`): 머리 우·발 좌(**가로 90°/270°** 사다리꼴 ROI)
   - 반면 COCO 사전학습 `yolo11*-seg.pt`는 정립(머리 위·발 아래) 사람 비중이 압도적이어서, 누워 있거나 90°/180° 회전된 자세에서 `conf < 0.5` (`no_person`) 누락 및 마스크 튐(ROI 밖 5% 오탐)이 발생한다.
2. **가설**
   - **H1 (모델 스케일 효과)**: GPU(`cuda:0`, RTX 4070) 환경에서 `yolo11n-seg` → `yolo11s/m/l-seg`로 키우는 것만으로 `no_person`과 마스크 경계 오차가 얼마나 줄어드는가?
   - **H2 (침상 도메인 파인튜닝 효과)**: `run_data/test_bed.mp4`에 대형 Teacher(`yolo11x-seg`, 다각도 추론)로 고품질 사람 폴리곤 마스크를 추출해 `yolo11n-seg` / `yolo11s-seg`를 파인튜닝하면, 베이스라인 대비 `coverage`, `f1`, `safe_fpr`이 유의미하게 개선되는가?
   - **H3 (4방향 회전 학습 효과)**: 학습 데이터(`train`)에 `0° / 90° / 180° / 270°` 회전 이미지·폴리곤을 함께 학습시키면, **런타임 프레임 회전 없이도** 세로 반전 영상과 가로 시연 웹캠 양쪽에서 안정적인 검출이 가능한가?

---

## 2. 실험 매트릭스 (베이스라인 vs 파인튜닝)

모든 실험은 동일한 운영 규칙(`classes=[0]`, `conf=0.5`, `mask > 0.5`, `outside_fraction=0.05`, `PatientSelector(min_iou=0.15)`, `BedExitState(3/5)`)으로 평가한다.

| 실험 ID | 구분 | 모델 / 학습 조건 | 추론 방향 | 목적 |
| :--- | :---: | :--- | :---: | :--- |
| **B1** | Baseline | Pretrained `yolo11n-seg.pt` | 원본 | 현행 운영 기준선 |
| **B2** | Baseline | Pretrained `yolo11s-seg.pt` | 원본 | 경량 상위 사전학습 비교 |
| **B3** | Baseline | Pretrained `yolo11m-seg.pt` | 원본 | 중형 사전학습 비교 |
| **B4** | Baseline | Pretrained `yolo11l-seg.pt` | 원본 | 대형 사전학습 상한 확인 (RTX 4070 GPU) |
| **B1-rot90** | Baseline+회전 | Pretrained `yolo11n-seg.pt` | 왼쪽 90° | 회전만으로 얻는 효과 참고 *(가로 웹캠 시연 비교용)* |
| **FT1** | Fine-tune | `yolo11n-seg` + `test_bed.mp4` (원본 방향 `0°`) | 원본 | 침상 도메인 파인튜닝 효과 (`n`) |
| **FT2** | Fine-tune | `yolo11n-seg` + `test_bed.mp4` (**4방향 `0/90/180/270°`**) | 원본 | **핵심 후보**: 런타임 회전 없이 전방향 누운 자세 대응 (`n`, CPU/GPU 겸용) |

---

## 3. 데이터 분할 및 Teacher 라벨링 (`01_autolabel_test_bed.py`)

1. **시간 블록 분할 (`train` / `val`)**: 연속 프레임 간 누수를 줄이기 위해 시간 블록 단위로 `train`/`val`을 분리하고, 4방향 회전(`0/90/180/270°`) 증강은 `train` split에만 적용한다.
2. **Teacher 자동 폴리곤 라벨링**: `yolo11x-seg.pt`(`retina_masks=True`, 다각도 `0/90/180/270°` 중 최고 신뢰도 각도 선택 후 원좌표 역회전)으로 마스크를 추출하고, 신뢰도 기준 미달 프레임은 빈 라벨로 넣지 않고 제외한다.

---

## 4. 파인튜닝 하이퍼파라미터 기본 레시피 (`02_train_yolo_seg.py`)

| 항목 | 설정값 | 선정 이유 |
| :--- | :---: | :--- |
| `task` / 클래스 | `segment`, `nc=1` (`0: person`) | 운영 코드 `classes=[0]` 계약 일치, 불필요 클래스 혼동 제거 |
| `imgsz` | `640` | 운영 추론 `imgsz=640`과 일치 |
| `epochs` / `patience` | `25~60` / `10~20` | 과적합 전 Early Stopping |
| `freeze` | `10` | 소규모 데이터셋에서 COCO 백본 일반화 특징 보존 |
| `cos_lr` | `True` | 안정적 미세조정 |

---

## 5. 단계별 실행 순서

```bash
PYTHON=/home/mu-02/venvs/rokey_venv/bin/python3
export PYTHONPATH="$(pwd)/src/part5_bed_exit:${PYTHONPATH}"

# 1) 베이스라인(B1~B4) 계측
$PYTHON src/part5_bed_exit/finetune/03_benchmark_seg_models.py \
  --device cuda:0 \
  --candidate B1_yolo11n_seg=src/part5_bed_exit/models/yolo11n-seg.pt \
  --candidate B2_yolo11s_seg=yolo11s-seg.pt \
  --candidate B3_yolo11m_seg=yolo11m-seg.pt \
  --candidate B4_yolo11l_seg=yolo11l-seg.pt \
  --output-dir src/part5_bed_exit/finetune/runs/01_baselines_cuda

# 2) test_bed.mp4 Teacher 자동 폴리곤 라벨링 + 0°/4방향 데이터셋 생성
$PYTHON src/part5_bed_exit/finetune/01_autolabel_test_bed.py \
  --video src/part5_bed_exit/run_data/test_bed.mp4 \
  --output-dir src/part5_bed_exit/finetune/data/test_bed_seg \
  --device cuda:0

# 3) 파인튜닝 실행 (FT1: 0°, FT2: 4방향)
$PYTHON src/part5_bed_exit/finetune/02_train_yolo_seg.py \
  --base-model src/part5_bed_exit/models/yolo11n-seg.pt \
  --data src/part5_bed_exit/finetune/data/test_bed_seg/dataset_0deg/dataset.yaml \
  --name ft1_yolo11n_seg_testbed_0deg --device 0 --epochs 25 --freeze 10

$PYTHON src/part5_bed_exit/finetune/02_train_yolo_seg.py \
  --base-model src/part5_bed_exit/models/yolo11n-seg.pt \
  --data src/part5_bed_exit/finetune/data/test_bed_seg/dataset_rot4/dataset.yaml \
  --name ft2_yolo11n_seg_testbed_rot4 --device 0 --epochs 25 --freeze 10

# 4) 베이스라인 vs 파인튜닝 일괄 벤치마크
$PYTHON src/part5_bed_exit/finetune/03_benchmark_seg_models.py \
  --device cuda:0 \
  --spec src/part5_bed_exit/finetune/experiments.template.json \
  --output-dir src/part5_bed_exit/finetune/runs/02_baseline_vs_finetuned
```
