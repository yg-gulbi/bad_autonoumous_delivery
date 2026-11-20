# Part 5 v1.4 검증

v1.4 신규 기능(비동기 반복 TTS, 전체 하이퍼파라미터의 JSON/YAML 파라미터화,
프레임/ROI 회전, 선택적 다중 모델 앙상블, `finetune/` 파이프라인)을 검증하는 테스트 트리다.
기존 `80805b4_v1_3/` 계약과의 하위 호환성은 `80805b4_v1_3/run_validation.sh`를 그대로 실행해 확인한다.

```bash
# v1.4 단위 테스트
bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh unit

# v1.4 ROS 2 비동기 TTS·파라미터·앙상블 통합 테스트
bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh integration
```

결과 산출물 경로:

```text
src/part5_bed_exit/results/1e0faac_v1_4/YYYYMMDD_HHMMSS_PROFILE[_실험명]/
├── execution.log
├── summary.json
├── unit_tests/pytest.xml
├── integration_tests/tts_and_params_pipeline.xml
└── ros_logs/
```
