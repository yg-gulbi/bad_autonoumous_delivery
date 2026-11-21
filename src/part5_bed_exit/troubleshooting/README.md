# Part 5 Troubleshooting 목록

Part 5 (`part5_bed_exit`) 개발·실험·통합 과정에서 발생한 문제와 원인, 조치 및 결과를 정리한 문서입니다.  
각 문서는 **"목표(원했던 것) → 원인 및 조치(해결 과정) → 결과 및 교훈"** 순서로 작성되어 있습니다.

1. [01_config_model_dir_and_integration_crash.md](./01_config_model_dir_and_integration_crash.md) — 관제 `main` 통합테스트 시 `model_dir` 상대경로 불일치 및 `.pt` 배치 문제
2. [02_single_video_finetune_overfitting.md](./02_single_video_finetune_overfitting.md) — 단일 영상(`test_bed.mp4`) 파인튜닝 시 대형 모델(`s`/`m`) 외부 일반화 급락
3. [03_ochuman_catastrophic_forgetting.md](./03_ochuman_catastrophic_forgetting.md) — OCHuman 단독 파인튜닝 시 단독 인물 Catastrophic Forgetting 및 `Recall` 급락
4. [04_postproc_sweep_direction_bias.md](./04_postproc_sweep_direction_bias.md) — Post-processing 스윕의 보수적 편향과 OFAT 실험 재설계
