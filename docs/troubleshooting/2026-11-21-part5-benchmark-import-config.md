# Part 5 벤치마크 import와 설정 경로 오류

- 프로젝트: bad_autonoumous_delivery Part 5
- 발생일: 2026-11-21
- 기록일: 2026-11-21
- 상태: 설정 경로 검증 완료, 일부 구버전 테스트 입력 미확보
- 관련 파일: [메서드 설정 템플릿](../../src/part5_bed_exit/config/methods.template.json)

## 1. 문제정의

### 기술적으로 관찰한 사실

저장소 루트에서 실행한 인라인 Python 코드가 `research.integration_benchmark.metrics` import 중에 멈췄다. 로컬 설정 사본과 메서드 설정 템플릿은 `model_dir` 해석 결과가 실제 모델 폴더와 달랐다.

### 원인

가상환경의 Python 검색 경로에 `src/part5_bed_exit`가 있어야 소스의 `research`를 import할 수 있다. 기존 모델 로더는 `model_dir` 상대경로를 JSON이 있는 디렉터리에서 해석한다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 이유 | 결과 |
| --- | --- | --- |
| 인라인 실행에서 `PYTHONPATH` 지정 | 현재 가상환경에서 연구 모듈을 import할 수 있다 | import 재현 및 해결 확인 |
| JSON별로 모델 폴더에 맞는 상대경로 사용 | 기존 모델 로더의 경로 규칙을 사용한다 | 설정 5개가 같은 폴더로 해석됨 |

## 3. 해결방법 실행

`config/methods.template.json`과 로컬 `run_data/test_input_sequence1_methods_v1.json`의 `model_dir`을 `../models`로 수정했다. 두 단계 아래에 있는 `research/configs` 원본은 `../../models`를 사용한다.

### 검증

- 현재 테스트를 버전별로 재실행해 pytest 170건 통과, 실패 4건, 수집 오류 2건을 확인했다. 기본 실행에서 건너뛴 4건은 별도로 활성화했고 원본 영상과 이전 산출물이 없어 중단됐다.
- 로컬 영상 1,024프레임에서 CPU Segmentation 벤치마크가 완료됐다. Part 3·5 모의 통합은 7건 모두 통과했다.

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자는 config 문제가 처리된 것으로 예상했으나 import 단계에서 실행이 중단되었다고 보고했다.

### AI 해석·제안

settings 파일만 배포해도 디렉터리 구조가 같으면 모델 경로를 해석할 수 있다. 인라인 실행에는 Python 모듈 검색 경로가 별도로 필요하다.

## 5. 정리

- 현재 결론: 기존 코드의 방식에 맞게 설정 경로를 수정했다.
- 다시 발생하면: 저장소 루트에서 인라인 Python 실행 전에 `PYTHONPATH="$PWD/src/part5_bed_exit${PYTHONPATH:+:$PYTHONPATH}"`를 적용하고 JSON 위치를 기준으로 `model_dir`를 확인한다.
- 다음에 반영할 것: 없음.
