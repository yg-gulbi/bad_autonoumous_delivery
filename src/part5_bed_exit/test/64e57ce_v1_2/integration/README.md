# 64e57ce MP4 우회 통합 테스트

`test_mp4_current_modules.py`는 웹캠 대신 `test_input_sequence1.mp4`를 사용해 현재
`create_method()` → segmentation 판단 → `BedExitState` 5프레임 투표 경로를 실행한다.

실제 모델 추론은 기본 테스트에서 제외하며 다음 환경 변수를 지정했을 때만 실행한다.

```bash
PART5_RUN_MP4_INTEGRATION=1 \
PYTHONPATH=src/part5_bed_exit \
python3 -m pytest \
  src/part5_bed_exit/test/64e57ce/integration/test_mp4_current_modules.py -q -s
```

