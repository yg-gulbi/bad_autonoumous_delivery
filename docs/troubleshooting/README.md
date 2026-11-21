# 트러블슈팅 기록

이 프로젝트에서 실제로 발생한 문제와 해결 과정을 건별로 남깁니다. 사용자의 직접 경험과 AI의 해석은 각 기록 안에서 구분합니다.

| 기록 | 상태 | 결과 |
| --- | --- | --- |
| [Part 5 실험 코드와 ROS 계약](2026-11-15-part5-integration.md) | 해결 | 네 전략 재사용, 5프레임 3/5·오류 미발행 일치 |
| [Part 5 외부 접근·검증 환경](2026-11-15-part5-validation-environment.md) | 일부 검증 대기 | PyAV 별도 환경 검증, Figma 한도·커널 연결 제한 기록 |
| [입력 영상 자동 연결](2026-11-15-part5-input-video.md) | 코드 수정 / 데이터 접근 검증 대기 | 모델과 입력 영상 구별, 다운로드 결과 자동 연결 |
| [첫 설치 셀·Matplotlib inline 충돌](2026-11-15-part5-matplotlib-inline.md) | 해결 / 커널 재시작 필요 | 실행 설치 셀 추가, Matplotlib 업데이트 후 inline 표시 확인 |
| [Part 5 colcon test가 pytest를 수집하지 못함](2026-11-16-part5-colcon-pytest-discovery.md) | 해결 | setuptools test extra를 선언해 60건 수집·통과 |
| [Part 5 실시간 웹캠 Tracked patient lost 오류](2026-11-16-part5-tracked-patient-lost.md) | 검증 대기 | 중심 거리 보조 추적 및 침대 ROI 재획득 추가 (`pytest` 23건 통과, 실웹캠 재확인 대기) |
| [part3_part5 rosbag 최종 안전 상태 누락](2026-11-19-part3-part5-rosbag-final-state-race.md) | 코드 수정 / 재실행 검증 대기 | 최종 안전 상태 3회 수신 후 recorder 종료하도록 동기화 |
| [part3_part5 rosbag ACK 구독 준비 경쟁 상태](2026-11-19-part3-part5-rosbag-ack-subscription-race.md) | 코드 수정 / 재실행 검증 대기 | 임무 전 mission·ack·command·fleet recorder 구독 확인 |
| [PART 04 새 goal_manager.py를 preflight가 읽지 못함](2026-11-19-part3-4j-goal-manager-format.md) | 해결 | SLOTS·CROSSROAD와 예전 GOALS를 실행 없이 읽고 차이 0건 확인 |
| [같은 구역 두 자리 사이 근접 정지](2026-11-19-part3-4j-slot-proximity.md) | 코드 수정 / 현장 검증 대기 | 같은 구역 출발·도착에만 0.45 m 적용, 실기 거리 확인 필요 |
| [헤드리스 크롬 캡처가 끝나지 않음 (SSE)](2026-11-19-part3-4j-headless-capture-sse.md) | 해결 (작업 도구) | DevTools 프로토콜로 4J 화면 4장 캡처 |
| [Part 5 시작 시 웹캠 미연결 종료 및 USB 재연결 보강](2026-11-20-part5-webcam-startup-reconnect.md) | 단위 테스트·통합 테스트 통과 / 실웹캠 분리 검증 대기 | 웹캠 미연결 생존·즉시 `release()`·1초 쿨다운 재연결 추가, 단위 51/51·통합 7/7 통과 |
| [part3_part5 통합 테스트 robot2/robot3 네임스페이스 불일치](2026-11-20-part3-part5-robot-namespace-mismatch.md) | 해결 | `fleet.yaml`(D-29) `robot_a=robot3`에 맞춰 명령 토픽 상수화 후 통합 테스트 7/7 PASS (15.0s, MCAP 104건) |
| [Part 5 벤치마크 import와 설정 경로 오류](2026-11-21-part5-benchmark-import-config.md) | 설정 경로 검증 완료 | JSON 5개가 같은 모델 폴더를 해석하고 실제 영상에서 추론 완료 |

새 기록 양식: [_template.md](_template.md)
