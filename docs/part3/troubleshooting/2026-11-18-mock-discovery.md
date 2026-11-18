# 로컬 mock 세 노드가 실행돼도 토픽이 수신되지 않음

bad_ws PART 03 · 2026-11-18 발생 및 기록 · 해결

## 1. 문제정의

격리한 ROS 도메인에서 관제와 mock 두 프로세스가 정상 시작됐지만, 관측 노드가 25초 동안 `/fleet/state`를 한 건도 받지 못했다. 셸에는 기존 Fast DDS 서버를 지정하는 `ROS_DISCOVERY_SERVER`가 남아 있었다.

## 2. 해결안

mock과 관측 프로세스가 같은 localhost 발견 설정을 상속하도록 실행 환경을 맞췄다.

## 3. 실행과 검증

```bash
unset ROS_DISCOVERY_SERVER ROS_STATIC_PEERS
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ROS_DOMAIN_ID=81
```

새 환경으로 launch와 구독자를 함께 시작하자 두 로봇이 모두 온라인이 됐다. 독립된 네 도메인에서 시나리오별 상태와 이벤트를 수집했다. [재현 명령과 관측값](../stage3-scenarios.md)

## 4. 경험의 재해석

사용자 경험 미기록. 이번 실행에서는 프로세스의 정상 시작과 DDS 연결 여부를 별도로 관측했다.

## 5. 정리

로컬 시험에서 토픽이 비면 모든 터미널의 발견 서버, 발견 범위, 도메인 ID를 함께 확인한다.
