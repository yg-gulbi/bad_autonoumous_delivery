# Part 5 야간 POC 작업 기록

이 디렉터리는 2026-11-16에 수행한 Part 5 병상 이탈 POC 작업을 재현하고 복구하기 위한 기록이다. 데이터셋 범위는 사용자 지시에 따라 Roboflow Bed Exit v4로 고정한다.

## 실행 순서와 커밋 경계

0. `00_사전상태_및_연구코드_분리.md`: 기존 미커밋 작업의 출처, 검증, 커밋 경계
1. `01_MVP_판정규칙_정합성.md`: 단일 ROI와 런타임 3/5 규칙 정합화
2. `02_Pose_MediaPipe_POC.md`: 독립 가상환경, 모델 준비, 실제 이미지 추론
3. `03_라벨_출력형식_비교.md`: 네 방법과 Roboflow 라벨 비교
4. `04_Roboflow_20장_검수.md`: 20장 검수와 대표 3장·코너케이스 선정
5. `05_담요조건_검증계획.md`: Roboflow 한계와 후속 검증 계획
6. `06_팀공유_요약.md`: 실험 결과표와 팀 공유용 결론

각 문서는 수행 명령, 입력, 산출물, 검증 결과, 한계, 복구 방법을 포함한다. 모델 가중치, 원본 데이터셋, 가상환경, 실행 결과 CSV·이미지는 저장소의 제외 규칙에 따라 커밋하지 않으며, 재생성 명령과 해시는 문서에 기록한다.

## 변경 범위

- 저장소: `/home/hyunwoong/workspaces/bad_autonoumous_delivery`
- 구현 대상: `src/part5_bed_exit/`
- 로컬 실행 자산: 저장소 내부 `.venv/`, `src/part5_bed_exit/models/`, `src/part5_bed_exit/research/data/`, `src/part5_bed_exit/results/`
- 저장소 밖 기존 `/home/hyunwoong/venvs/rokey_venv`는 읽기와 기준 확인에만 사용하고 수정하지 않는다.

