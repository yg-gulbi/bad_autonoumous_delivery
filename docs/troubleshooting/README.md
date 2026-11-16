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

새 기록 양식: [_template.md](_template.md)
