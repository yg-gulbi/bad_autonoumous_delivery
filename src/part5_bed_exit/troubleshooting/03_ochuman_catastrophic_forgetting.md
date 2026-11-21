# 03. OCHuman 단독 파인튜닝 시 Catastrophic Forgetting 및 `Recall` 급락

## 1. 내가 원했던 것
* 침대 위에서 이불·손·팔 등이 사람 몸 일부를 가리더라도(`Occlusion`) 사람의 외곽 경계를 끝까지 구별할 수 있도록, 가림 특화 데이터셋(OCHuman)을 활용해 세그멘테이션 성능을 개선하고자 했다.

## 2. 문제 원인 및 조치
* **시도 (`FT5_n_ochuman`)**:
  * OCHuman (`ochuman_seg`, Train 2,500장 / Val 500장)을 YOLO-seg 포맷으로 변환하고, `yolo11n-seg.pt`에 `freeze=0`, `degrees=180.0`, `flipud=0.5`, `fliplr=0.5`로 30 epoch 풀 파인튜닝을 진행했다.
* **문제 원인**:
  * OCHuman은 **"여러 사람이 심하게 겹친(`MaxIoU > 0.5`) 사진"만** 모아둔 데이터셋으로, 혼자 누워 있거나 가림이 적은 일반 단독 인물 이미지가 거의 없다.
  * 일반 COCO `person` 데이터 없이 OCHuman 단독으로 `freeze=0` 풀 파인튜닝을 진행하면서, 기존 모델이 갖고 있던 단독 인물 인식 능력이 손상(**Catastrophic Forgetting**)되고 전체 confidence 점수가 하락했다.
* **조치**:
  * `coco_ochuman_mix` (1:1 혼합) 테스트(`FT6`) 후에도 도메인 성격이 너무 다른 데이터셋 혼합은 한계가 있다고 판단해, 이후 학습 데이터는 **COCO `person` 전용 + 회전 증강**으로 단순화하기로 결정.

## 3. 결과 및 교훈
* **`test_bed.mp4` 벤치마크 결과 (`FT5_n_ochuman` vs `B1_n` / `B3_m`)**:
  * `SAFE_FPR`: `0.142 (B3_m)` / `0.135 (B1_n)` → **`0.034`** (오탐률 대폭 감소, `Precision=0.967`)
  * `Coverage` (`no_person`): `0.990 (10)` → **`0.721 (286)`** (미검출 급증)
  * `Recall`: `0.881 (B3_m)` / `0.832 (B1_n)` → **`0.398`** (`Events=8/9`)
* **외부 이미지(`test_images/` 45장) confidence 민감도**:
  * `conf=0.25`: `32/45 (71.1%)` → `conf=0.15`: `39/45 (86.7%)`
* **교훈**: 분포가 특수한 데이터셋을 단독으로 `freeze=0` 학습하면 기존 일반 `person` 지식을 잊어 `Recall`이 급락한다. 범용 `COCO person` 데이터를 유지한 채 회전 증강을 적용하는 것이 안전하다.
