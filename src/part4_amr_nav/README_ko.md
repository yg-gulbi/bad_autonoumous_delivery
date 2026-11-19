# hospital_nav

관제 콘솔에서 목적지를 보내면 로봇이 Nav2로 이동하고,
이동하는 동안 **1초마다 남은 거리(m)와 진행률(%)**을 관제 콘솔로 보냅니다.

## 모듈
| 파일 | 역할 |
|---|---|
| `goal_manager.py` | A~F 좌표 + 로봇별 도크 좌표(`DOCKS`) |
| `dock_manager.py` | Create3 dock/undock, dock_status, 자동 초기위치 |
| `navigation_manager.py` | Nav2 NavigateToPose 송신/취소, 남은 거리 수신 |
| `mission_manager.py` | 이동 → 진행률 보고 → 도착 / 정지 / 재개 / 취소 |
| `command_receiver.py` | 로봇마다 실행. 관제 명령 수신 |
| `keyboard_control.py` | 관제 콘솔. 명령 전송 + 진행률 표시 |

## 실행
```bash
# 로봇마다 (위치 추정 + Nav2 가 켜진 상태에서)
ros2 run hospital_nav command_receiver --robot robot2
ros2 run hospital_nav command_receiver --robot robot3

# 관제 콘솔
ros2 run hospital_nav keyboard_control --robots robot2 robot3
```
콘솔 명령: `A UNDOCK`, `A F`, `A DOCK`, `robot2 F`, `robot2 F 2`(응급도 1~3), `robot3 PAUSE` / `RESUME` / `CANCEL`, `S`(상태표), `Q`(콘솔 종료).

## 두 대 동시 운용 (PC 분리 구성)
```
            서버 PC (.31, discovery 서버) ── 관제 콘솔
              /                        \
   PC1 (robot2 .229)               PC2 (robot3 .103)
   localization/Nav2/RViz           localization/Nav2/RViz
   command_receiver --robot robot2  command_receiver --robot robot3
```
- PC1 `ROS_DISCOVERY_SERVER` = `.31` + `.229`, PC2 = `.31` + `.103` (ROS_DOMAIN_ID=3)
- 로봇 원본 토픽은 각 PC 에서만 보이고, `hospital_nav` 명령/상태 토픽은 서버 PC 를 통해 공유됨
- 관제 콘솔은 서버 PC 에 연결된 어느 PC 에서 실행해도 됨: `keyboard_control --robots robot2 robot3` (A=robot2, B=robot3)

## 토픽 (std_msgs/String, JSON)
| 토픽 | 방향 | 내용 |
|---|---|---|
| `/<robot>/hospital_nav/command` | 관제 → 로봇 | `F`, `F 3`, `MOVE F 3`, `PAUSE`, `RESUME`, `CANCEL` |
| `/<robot>/hospital_nav/status` | 로봇 → 관제 | `type=STATUS` 이벤트, 1초마다 `type=STATE` (상태 + `battery`: percent, voltage, status) |

이벤트: `MISSION_ACCEPTED`, `NAVIGATING`, `PROGRESS`(remaining_m, percent), `PAUSED`, `RESUMED`,
`ARRIVED`(percent=100, goal_error_m), `MISSION_FAILED`, `MISSION_CANCELED`.

진행률 = `(1 - 남은 경로 길이 / 처음 경로 길이) × 100`. 정지 후 재개해도 처음 경로 길이를 기준으로 이어집니다.

## 운용 순서
1. 도크 위에서 `command_receiver` 시작 → 도크 위면 `DOCKS` 좌표를 AMCL 초기위치로 자동 발행 (`DOCKED` 직후에도 다시 발행)
2. `A A 2` 처럼 이동 명령 → 로봇이 `dock_status` 로 판단
   - 도크 위: `UNDOCKING` → `UNDOCKED` → `LOCALIZING`(Nav2 active 최대 90 s, AMCL 위치 최대 10 s 대기) → `NAVIGATING`
   - 도크 밖: 바로 `NAVIGATING` (Nav2 준비 전이면 거절)
3. `A DOCK` → 도크 앞 0.6 m 이동 → Create3 dock → `DOCKED` (이미 도크 위면 무시)
4. `A UNDOCK` 은 언독만 하고 대기 (선택)

RViz 는 로봇당 하나만 (두 개면 WiFi 과부하로 Nav2 가 멈춤).

## 주의
- 로봇끼리 길을 양보하거나 복도 점유를 막는 기능은 **없습니다**. 두 대가 같은 복도를 쓰면 사람이 지켜보세요.
- `command_receiver` 를 Ctrl+C / SIGTERM 으로 끄면 진행 중인 주행을 취소합니다(최대 10초 대기).
- 좌표는 임시값입니다.
