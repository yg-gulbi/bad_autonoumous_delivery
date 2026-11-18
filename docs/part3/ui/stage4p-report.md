# 단계 4P · 실제 로봇 연결 준비

2026-11-18. 실기용 실행 파일은 관제와 화면 두 노드를 띄운다. 그 상태에서 별도 프로세스로 띄운 Robot A·B를 preflight가 모두 OK로 판정했다. 같은 관제와 화면에서 mock만 종료하자 로봇별 필수 항목 6개가 모두 누락으로 바뀌었다. 강의실 로봇 주행은 다음 단계 4의 현장 시험으로 남는다. [현장 순서](../REAL_TEST.md)

## 연결 점검 실측

팀 가상환경에서 `ROS_DISCOVERY_SERVER`·`ROS_SUPER_CLIENT`·정적 피어를 해제했다. 도메인 87, `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, `rmw_fastrtps_cpp`를 사용했다. 추가로 Fast DDS의 UDPv4 인터페이스를 `127.0.0.1`로 제한했다. 화면은 루프백 8417에서 열었고 DB·로그·빌드·설치 위치는 임시 디렉터리를 사용했다. 실제용 기본 화면 주소는 [localhost:8090](http://localhost:8090/)이다.

| 실행 조건 | 관측 | preflight 종료 코드 |
|---|---|---|
| `real.launch.py` + 별도 주행 mock 2개 | Robot A와 B 모두 OK. 유일한 명령 발행자는 `/fleet_manager`. | 0 · 실제 실행 가능 |
| 같은 상태에서 `/robot2/keyboard_control`이 명령 토픽 발행자로 등록 | 메시지를 보내기 전에도 충돌 경고에 해당 노드와 `/fleet_manager` 이름이 함께 표시됐다. | 1 · 실제 실행 안 됨 |
| 동일한 관제·화면에서 mock과 시험 발행자만 종료 | `/robot2`, `/robot3` 각각 STATE, 위치, 배터리, 도크, 위험 센서, 액션 서버를 빠진 이유로 출력했다. | 1 · 실제 실행 안 됨 |

| 로봇 | hospital_nav STATE | amcl_pose (m) | 배터리 | 도크 | 위험 센서 | navigate_to_pose |
|---|---|---|---|---|---|---|
| Robot A · /robot2 | IDLE | (−0.600, 0.400) | 80.0% | 도크 밖 | 0건 수신 | 서버 있음 |
| Robot B · /robot3 | IDLE | (−0.800, −5.100) | 60.0% | 도크 밖 | 0건 수신 | 서버 있음 |

- 두 명령 토픽을 별도로 구독해 점검하는 동안 로봇 명령 **0건**을 확인했다. Nav2는 그래프의 **액션 서버** 등록을 조회한다. 목표 달성이나 실제 제동은 현장 시험 항목이다.
- 실기용 launch 자체가 띄운 노드는 `/fleet_manager`, `/ui_server` 두 개였다. API에서 실기 모드, 시연·시험 도구 끔을 확인했다. 시험 조작 요청은 HTTP 403이었다. 설치된 `fleet.yaml`의 침상 감시 시작 정책은 `true`, DB 기본 인자는 `~/.bad/bad_real.db`였다.
- 선택 장치가 없을 때 침상 감시는 `0.0 Hz (0건, 마지막 값 없음)`, 두 분류 결과는 각각 ‘없음 — 30초 규칙으로 동작’으로 표시됐다. 관제 A~F 좌표와 PART 04의 GOALS는 모두 허용 거리 안이었다.
- 시연에서 두 분류기를 켜고 침상 정상 송신을 시작하자, 같은 도구가 분류 결과 발행자 두 개와 침상 보고 84건(전체 관측 10초 기준 8.4 Hz, 마지막 값 정상)을 읽었다.
- 발견 서버의 포트 검사에는 로컬 UDP 소켓의 주소·포트·소유 프로세스를 사용했다. `127.0.0.1:31947`에서 Fast DDS 서버를 켰을 때 `fast-discovery-server`, 종료 후 ‘UDP 수신 포트 없음’을 확인했다. 이 시험에서도 ROS 노드의 발견 서버 환경 변수는 해제한 상태였다. [시험 PC에서 사용한 Fast DDS 인자](../troubleshooting/2026-11-18-fastdds-discovery-options.md)

## 시연과 막힘 카드

동일한 격리 환경에서 `demo_mock.launch.py`가 관제·화면·주행 mock 2대·분류 mock 2대를 띄웠다. 화면 서버의 요청 경로로 A 구역 호출을 등록하고 Robot A의 확인 대기에서 수령을 확인했다. `M-0001`은 완료됐고 Robot A는 홈으로 복귀해 대기가 됐다. 작업 상세의 정류장 기록에서 확인자 ‘격리 시험’을 읽었다.

| 카드 생성 때의 분류 | 운영자에게 보이는 첫 선택지 |
|---|---|
| PERSON | 비켜 줌, 다시 출발 |
| OBJECT | 치웠음, 다시 출발 |
| NONE, UNKNOWN, 결과 없음 | 확인함, 다시 출발 |

세 문구의 선택지 ID는 모두 `retry`다. 기존 흐름의 재출발·되돌아가기·보류 동작과 분류별 카드 생성 시점을 회귀 테스트로 확인했다.

## 빌드와 pytest

두 패키지의 소스를 임시 빌드 공간에 복사하고 colcon으로 설치했다. 설치된 진입점으로 실제용·시연용 launch와 preflight를 실행했다. 두 패키지 빌드 성공, 실패 0개. 현장 PC의 기본 워크스페이스에 적용하는 빌드 명령은 [현장 순서 2절](../REAL_TEST.md)에 있다.

팀 가상환경과 Jazzy를 불러온 뒤 사용한 담당 패키지 테스트 명령이다.

```bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ROS_DOMAIN_ID=87
PYTHONPATH="src/part1_ui_dashboard:src/part3_fleet_manager:$PYTHONPATH" \
  python -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test src/part3_fleet_manager/test
```

- PART 01·03 합계 **328 통과, 0 실패, 0 건너뜀**. 새 테스트 3개가 목표 좌표의 비실행 파싱, 5 cm 경계, 잘못된 UDP 소유자·주소·포트를 확인한다. 기존 테스트 39와 막힘 선택지 테스트에 버튼 문구 검사를 추가했다.
- 저장소의 PART 05 테스트까지 포함해 **414 통과, 0 실패, 0 건너뜀**. PART 05 선택 영상 테스트에 필요한 PyAV는 이 PC의 다른 가상환경에 설치된 모듈을 테스트 프로세스의 임시 경로로 읽었다.

## 변경 파일

| 위치 | 파일 |
|---|---|
| `src/part1_ui_dashboard/` | `launch/real.launch.py`, `part1_ui_dashboard/ui_server_node.py` |
| `src/part3_fleet_manager/` | `package.xml`, `setup.py`, `part3_fleet_manager/preflight.py`, `part3_fleet_manager/preflight_checks.py`, `part3_fleet_manager/mock_hospital_nav.py` |
| `src/part3_fleet_manager/part3_fleet_manager/core/` | `decisions.py`, `fleet_core.py` |
| `src/part3_fleet_manager/test/` | `test_preflight.py`, `test_fleet_core.py` |
| `docs/part3/` | `REAL_TEST.md`, `ui/stage4p-report.md`, `troubleshooting/2026-11-18-fastdds-discovery-options.md` |

## BRIEF 밖에서 정한 사항과 현장 확인 범위

- 명령 발행자 충돌, 필수 로봇 항목 누락, 설정한 발견 서버의 로컬 포트를 확인할 수 없는 경우 종료 코드 1을 쓴다. 격리 시험을 위해 발견 서버 **미설정**과 LOCALHOST 범위는 경고로 표시한다. 침상 감시와 장애물 분류는 수신 상태를 따로 보고한다.
- 실기용 launch에 기존 UI 설정 파일을 지정하는 `ui_config` 인자를 추가해 같은 실행 파일을 격리 포트에서도 검증했다. mock의 도크·위험 센서는 1 Hz로 도크 밖·감지 0건을 발행한다. mock의 Nav2 액션 서버는 그래프 확인용이고 이동은 기존 hospital_nav 경로로 수행한다.
- 현장 터미널 명령은 팀이 실제 연결에 성공한 `/usr/bin/fastdds` 방식을 기준으로 적었다. 이번 검증은 루프백에서 완료했으며 현장 무선 연결, 실물 센서 값, 물리 주행·정지는 현장 확인 대상이다.
