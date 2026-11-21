# 01. 관제 `main` 통합테스트 시 `model_dir` 경로 불일치 및 `.pt` 배치 문제

## 1. 내가 원했던 것
* 관제 쪽(`main` 브랜치)에서 `part5_bed_exit` 패키지를 받아 별도 코드 수정 없이 바로 통합테스트를 실행할 수 있는 상태.
* Git에 포함되지 않는 `.pt` 모델 가중치도 `README.md` 안내만으로 누구나 동일 경로에 내려받거나 배치해 구동할 수 있도록 정리.

## 2. 문제 원인 및 조치
* **원인 1 (`methods.template.json`의 `model_dir` 상대경로 불일치)**
  * [`pretrained.py`](../part5_bed_exit/pretrained.py)의 `read_config()`는 `model_dir`이 상대경로일 경우 **실행 작업 디렉토리가 아니라 JSON 설정 파일(`config/`) 위치를 기준**으로 해석한다.
  * 커밋 `04b06bb`에서 `cam_roi.json`과 `cam_roi_test.json`은 `"model_dir": "../models"`로 수정되었으나, 템플릿 파일인 [`config/methods.template.json`](../config/methods.template.json)만 `"model_dir": "src/part5_bed_exit/models"`로 남아 있어 `config/src/part5_bed_exit/models`를 찾다가 실패할 수 있었다.
* **원인 2 (`main` 브랜치 미반영 및 `.pt` 미다운로드)**
  * `.pt` 가중치 파일은 Git 추적 제외 대상이며, 최신 경로 수정 커밋(`04b06bb` 등)은 `exp/part5-seg-benchmark`에만 있고 `origin/main`에는 아직 머지되지 않은 상태(`3a21d19` Revert 이후)였다.
  * [`README.md`](../README.md) 24~37행에는 `downloads.py`의 `download_models("src/part5_bed_exit/models")` 실행 안내가 이미 포함되어 있었으나, CLI 엔트리포인트(`download.py`)가 아닌 Python one-liner 형태라 누락되기 쉬웠다.
* **조치**
  1. [`config/methods.template.json`](../config/methods.template.json)의 `model_dir`을 `"../models"`로 수정해 `cam_roi.json` · `cam_roi_test.json`과 동일 기준으로 통일.
  2. v1.4 단위·통합 검증 스크립트(`test/1e0faac_v1_4/run_validation.sh`)를 재실행해 회귀 여부 확인.

## 3. 결과 및 교훈
* **검증 결과**:
  * `bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh unit`: **39/39 PASS** (0.60s)
  * `bash src/part5_bed_exit/test/1e0faac_v1_4/run_validation.sh integration`: **3/3 PASS** (0.69s)
* **교훈**:
  * 설정 템플릿 파일(`methods.template.json`)도 실제 config와 동일하게 상대경로(`../models`)를 유지해야 복사 후 즉시 동작한다.
  * 관제 통합 시에는 `main` 머지 여부와 `src/part5_bed_exit/models/` 내 `.pt` 존재 여부를 함께 체크해야 한다.
