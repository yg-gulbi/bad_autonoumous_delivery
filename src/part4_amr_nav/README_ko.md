# hospital_nav

관제 콘솔에서 목적지를 보내면 로봇이 Nav2로 이동하고,
이동하는 동안 **1초마다 남은 거리(m)와 진행률(%)**을 관제 콘솔로 보냅니다.

## 모듈
| 파일 | 역할 |
|---|---|
| `goal_manager.py` | A~F 방마다 자리 2개(`SLOTS`) + 로봇별 도크 좌표(`DOCKS`) |
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
| `/<robot>/hospital_nav/obstacle_image` | 로봇 → 관제 | Nav2 ABORTED 때 찍은 사진 1장 (CompressedImage). 콘솔이 `~/hospital_nav_captures/` 에 jpg 저장 |

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

## 방마다 자리 2개
| 방 | 1번 (안쪽) | 2번 (입구쪽) |
|---|---|---|
| A | (-2.383, 1.027) | (-2.458, 0.282) |
| B | (-2.500, -1.250) | (-2.600, -2.800) |
| C | (-2.758, -5.123) | (-2.738, -4.573) |
| D | (-1.000, 0.800) | (-0.750, -0.150) |
| E | (-0.400, -1.400) | (-0.500, -2.950) |
| F | (-1.400, -5.400) | (-1.350, -4.700) |

- 관제에서 `B A` → 콘솔이 다른 로봇이 쓰는 자리를 보고 빈 자리(A1 먼저, 없으면 A2)를 골라 `MOVE A2 1` 로 보냄
- `B A2` 처럼 자리 직접 지정 가능 (사용 중이면 경고만)
- 도착하면 둘 다 `ARRIVED` (destination=A, slot=1/2), STATE 에도 `slot` 포함
- 자리 판단은 관제 콘솔이 함 → 다른 곳에서 `A` 만 보내면 1번 자리로 감

## 교차로 경유 (B, E)
B·E 는 복도 옆 좁은 문으로만 드나들어서, B·E 로 들어갈 때와 B·E 에서 다른 곳으로 나갈 때
교차로 중앙 (-1.50, -2.18) 에 먼저 들러 방향을 맞춘 뒤 곧장 들어가거나 나갑니다 (`VIA_PASSED` 이벤트).

## 장애물 보고 (Nav2 ABORTED)
Nav2 가 **첫 복구 동작을 시작하는 순간**(막혀서 멈춘 직후, 아직 막힌 쪽을 보고 있을 때) 카메라 사진 1장·뎁스 1장·라이다 스캔 1개를 **한 번만** 받아 보관합니다.
그 뒤 ABORTED 가 나면 `MISSION_FAILED` 후 보관한 것을
`OBSTACLE` 이벤트로 보고합니다 (캡처 최대 3초, 평소에는 카메라 구독 없음). 복구 후 길이 열려 도착하면 보관한 것은 버리고 아무것도 보내지 않습니다.
복구 없이 바로 ABORTED (예: costmap 갱신 시간 초과) 는 장애물이 아니므로 `MISSION_FAILED` 만 보냅니다.
`robot_pose`(x, y, yaw_deg), `depth_m`(화면 중앙 거리), `lidar_m`·`bearing_deg`(전방 ±45° 최근접), `map_x`·`map_y`(장애물 지도 좌표), `captured`.
상태는 FAILED 라 다음 명령을 바로 받습니다.

## 주의
- 로봇끼리 길을 양보하거나 복도 점유를 막는 기능은 **없습니다**. 두 대가 같은 복도를 쓰면 사람이 지켜보세요.
- `command_receiver` 를 Ctrl+C / SIGTERM 으로 끄면 진행 중인 주행을 취소합니다(최대 10초 대기).
- 좌표는 임시값입니다.
