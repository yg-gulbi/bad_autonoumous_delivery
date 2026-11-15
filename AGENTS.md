# Repository Guidelines (`bad_ws`)

## 1. 워크스페이스 역할 및 패키지 경계 (`src/`)
본 저장소는 병원 다중 AMR 자율주행 이동 지원 시스템의 5개 ROS 2 파트를 단일 colcon 워크스페이스(`bad_ws/src/`)로 분리한 협업 저장소입니다.

| 패키지명 | 담당 파트 | 핵심 역할 | 인터페이스 상태 |
| --- | --- | --- | --- |
| `part1_ui_dashboard` | PART 01 | 관제실 Web 대시보드 · `rosbridge` 연동 | 미확정 (뼈대만 구성) |
| `part2_qr_verifier` | PART 02 | 손목·물품·인계 QR 인식 및 대조 노드 | 미확정 (뼈대만 구성) |
| `part3_fleet_manager` | PART 03 | 중앙 관제·배차·공용도로 상태 송신·예외 제어 | 미확정 (뼈대만 구성) |
| `part4_amr_nav` | PART 04 | Nav2 자율주행·공용도로 대기/진입 판정 노드 | 미확정 (뼈대만 구성) |
| `part5_bed_exit` | PART 05 | 별도 웹캠 기반 침대 이탈 감지 노드 | **확정** (`/patient/bed_exit`) |

## 2. 파트 간 협업 및 수정 원칙
- **담당 파트 격리**: 각 작업자는 본인 담당 패키지(`src/part<N>_*/`) 내부에서 구현·테스트하며, 타 파트 패키지를 사전 합의 없이 수정하지 않습니다.
- **ICD 계약 준수**:
  - `PART 05` 출력은 `/patient/bed_exit` (`std_msgs/msg/Bool`, `true=이탈 감지`, `false=정상`)로 확정되어 있습니다.
  - `PART 01~04` 토픽·서비스 및 커스텀 메시지 후보(`MissionReq`, `QrVerify`, `FleetState`)는 팀 합의 후 확정합니다.
- **Part 5 안전 동작 유지**: `part5_bed_exit/bed_exit_state.py`의 5프레임 다수결(3회 이상 `True`) 판정과 카메라/메서드 예외 시 이력 초기화(거짓 정상 판정 발행 금지) 정책을 임의로 우회하지 않습니다.

## 3. 빌드 · 테스트 · Git 규칙
- 빌드 및 테스트:
  - 전체 빌드: `colcon build --symlink-install`
  - Part 5 단위 테스트: `PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q`
- **커밋 금지 대상**: `build/`, `install/`, `log/`, `.env`, 모델 가중치(`*.pt`, `*.onnx`, `*.engine`), 테스트 영상(`*.mp4`, `*.avi`)은 커밋하지 않습니다.
- 개발·디버깅 중 발생한 문제는 `docs/troubleshooting/`에 `_template.md` 양식으로 건별 기록하고 `docs/troubleshooting/README.md` 표에 링크를 추가합니다.
