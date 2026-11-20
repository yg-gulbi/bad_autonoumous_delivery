# PART 05 모델 파인튜닝 및 베이스라인 비교 실험 폴더 (`finetune/`)

병합된 핵심 ROS 노드(`part5_bed_exit/`)는 유지한 채, **사전학습 Segmentation 베이스라인 모델들(`B1~B4`)** 과 **`run_data/test_bed.mp4` 기반 파인튜닝 모델들(`FT1~FT2`)** 을 동일 기준으로 구축·학습·비교하기 위한 전용 공간입니다.

자세한 실험 가설, 매트릭스, 실행 명령은 **[EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)** 를 참고하세요.

## 폴더 구조

```text
src/part5_bed_exit/finetune/
├── EXPERIMENT_PLAN.md          # 실험 설계서 (B1~B4 vs FT1~FT2, 단계별 실행 순서)
├── experiments.template.json   # 일괄 벤치마크 후보 정의 JSON
├── 01_autolabel_test_bed.py    # Step 1: test_bed.mp4 자동 폴리곤 라벨링 및 0°/4방향 데이터셋 생성
├── 02_train_yolo_seg.py        # Step 2: YOLO-seg 파인튜닝 및 train_summary.json 기록
├── 03_benchmark_seg_models.py  # Step 3: test_bed.mp4 + 5프레임 다수결 공식 지표 일괄 벤치마크
├── data/                       # 학습 데이터셋 (.gitignore)
├── runs/                       # 학습 결과 & 벤치마크 산출물 (.gitignore)
└── eval_methods/               # 실시간 웹캠/노드 직접 비교용 외부 메서드
```

## 학습 완료 모델 운영 적용 (`pretrained.py` `"weights"` 키)

선정된 `best.pt`를 `src/part5_bed_exit/models/`에 복사하고 `config/cam_roi.json`에 `"weights"`만 추가하면 운영 코드 수정 없이 바로 교체됩니다:

```json
{
  "method": "segmentation",
  "model_dir": "../models",
  "weights": "ft2_yolo11n_seg_best.pt",
  "device": "cuda:0",
  "roi": [...]
}
```
