# 04. Post-processing 스윕의 보수적 편향과 OFAT 실험 재설계

## 1. 내가 원했던 것
* 모델 재학습 없이 후처리 파라미터(`confidence`, `mask_threshold`, `outside_fraction`, `BedExitState` 투표 수) 조정만으로 **`Recall`을 올리고 `SAFE_FPR`을 낮추는 최적 조합**을 찾고자 했다.

## 2. 문제 원인 및 조치
* **원인**:
  * 기존 스윕 결과([`03_param_voting_sweep/sweep_results.csv`](../finetune/runs/03_param_voting_sweep/sweep_results.csv), 320개 조합)를 점검한 결과, 탐색 범위가 전부 **보수적(`SAFE_FPR` 감소) 방향**에만 치우쳐 있었다:
    * `outside_frac`: `0.05`, `0.08`, `0.10`, `0.12` (`0.05` 미만인 `0.01~0.03` 미탐색)
    * `conf`: `0.35`, `0.50` (`0.15~0.25` 미탐색)
    * `mask_thr`: `0.50`, `0.60` (`0.35~0.40` 미탐색)
    * `vote`: `3/5`, `4/5`, `5/7`, `6/9` (`2/3` 등 민감 투표 미탐색)
  * 여러 옵션을 동시에 바꾸면 어떤 변수가 실제로 `Recall`이나 `SAFE_FPR`을 움직였는지 분리해 해석하기 어려웠다.
* **조치**:
  * `yolo11n-seg.pt` (pretrained 기본 설정)을 통제 기준점으로 두고 **한 번에 1개 변수씩만 바꾸는(OFAT) 3단계 실험**으로 재설계:
    1. Phase 1: 단일 기본 모델의 Post-processing 한계 측정 (`outside_fraction` / `confidence` / `mask_threshold` / `vote` 단독 변화)
    2. Phase 2: `COCO person` 전용 + 회전 증강 학습 (`test_bed.mp4` 미사용)
    3. Phase 3: 회전 TTA 및 `Seg + Pose` 앙상블

## 3. 결과 및 교훈
* **기존 스윕에서 확인된 대표 트레이드오프 (`B1_yolo11n_seg`, `conf=0.35`, `outside_frac=0.05`)**:
  * `vote=3/5 → 4/5` 변경 시 `SAFE_FPR`은 `0.135 → 0.115`로 감소, `Recall`은 `0.855 → 0.838`로 소폭 하락 (`F1: 0.889 → 0.883`).
* **교훈**: 후처리 스윕을 할 때는 `SAFE_FPR↓` 방향뿐 아니라 `Recall↑` 방향(`outside_frac < 0.05`, `conf < 0.35`)도 반드시 포함해 한 번에 한 축씩 한계치를 먼저 확인해야 한다.
