# 노트북 첫 설치 셀 누락과 RcParams._get 오류

- 프로젝트: BAD Part 5
- 발생일: 2026-11-15
- 기록일: 2026-11-15 (Asia/Seoul)
- 상태: 해결 (실행 중인 사용자 커널은 재시작 필요)
- 관련 파일·Issue·PR·커밋: [이미지 노트북](../../src/part5_bed_exit/notebooks/part5_single_image.ipynb), [전체 노트북](../../src/part5_bed_exit/notebooks/part5_bed_exit.ipynb), [의존성](../../src/part5_bed_exit/requirements.txt)

## 1. 문제정의

### 기술적으로 관찰한 사실

사용자는 첫 셀이 사라졌고 기존 노트북에서 `'RcParams' object has no attribute '_get'` 오류가 발생했다고 보고했다. 디스크의 첫 import 셀은 존재했지만 설치 명령은 Markdown 설명에만 있었다. rokey_venv는 /usr/lib/python3/dist-packages의 Matplotlib 3.6.3과 가상환경의 matplotlib-inline 0.2.2를 함께 사용했다. IPython inline 활성화에서 같은 AttributeError를 재현했다.

### 원인

matplotlib_inline/backend_inline.py의 통합 코드가 matplotlib.rcParams._get을 호출하지만 Matplotlib 3.6.3에는 해당 메서드가 없다. 별도 검증 환경의 최신 Matplotlib에서는 나타나지 않았던 커널 환경 차이다. 설치할 수 있는 시작 코드 셀이 없어 사용자가 환경을 맞추기 어려웠다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| RcParams의 내부 메서드를 임의 추가 | 라이브러리 간 버전 차이를 숨김 | 보류 |
| Matplotlib 업데이트와 의존성 점검 셀 추가 | 실제 충돌을 해소하고 재실행 방법 제공 | 선택 |

## 3. 해결방법 실행

AI가 rokey_venv의 Matplotlib을 3.11.2로 업데이트했다. 시스템 Matplotlib은 제거하지 않았다. requirements의 Matplotlib 하한을 3.10으로 올리고 matplotlib-inline 0.2.x를 명시했다. 두 노트북에 현재 커널 Python으로 requirements를 점검·설치하는 실행 셀을 추가했다. 설치 후 및 이전 Matplotlib이 메모리에 남았을 때는 재시작 안내로 실행을 중단한다. 사용자 노트북의 기존 코드·커널 선택은 유지했다.

### 검증

- 수정 전 IPython inline 활성화에서 AttributeError 재현.
- 수정 후 새 rokey_venv 프로세스에서 Matplotlib 3.11.2와 RcParams._get 존재 확인.
- matplotlib_inline backend import, configure_inline_support와 figure 표시 성공.
- 두 노트북 모든 Python 셀 구문 검사, 검증 환경에서 첫 셀 의존성 충족 확인.
- 사용자에게 열려 있는 기존 커널은 AI가 재시작하지 않았으며 직접 Restart Kernel 필요.

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자는 첫 셀 누락과 RcParams._get 오류를 보고했다. 이외 사용자 경험 미기록.

### AI 해석·제안

일반 Python 또는 Agg 그래프 출력 검증만으로 노트북 inline 백엔드 호환성을 확인할 수 없다. 실제 선택된 커널과 같은 환경에서 inline import·표시를 검사해야 한다.

## 5. 정리

- 현재 결론: 재현한 버전 충돌을 수정하고 설치 셀을 추가했다.
- 다시 발생하면: sys.executable, matplotlib.__version__, matplotlib.__file__ 확인 후 커널을 재시작한다.
- 다음에 반영할 것: 노트북 검증 시 inline 표시 경로도 확인한다.
