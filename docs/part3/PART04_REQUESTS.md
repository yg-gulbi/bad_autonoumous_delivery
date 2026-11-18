# PART 04 변경 요구사항 (관제 → PART 04)

- 작성: 2026-11-18 · PART 03 관제 담당 김준성
- 기준 코드: main `6931df3` (11/18 10:28) `src/part4_amr_nav/hospital_nav/`
- 관제는 이 코드의 `/<ns>/hospital_nav/command`와 `/<ns>/hospital_nav/status`로 붙습니다. 관제 쪽 상세는 `docs/part3/BRIEF.md` 3.3절에 있습니다.

## 관제가 코드를 보고 정한 것 (PART 04 작업 없음)

| 항목 | 정한 내용 |
|---|---|
| 로봇 namespace | Robot A = `robot2`, Robot B = `robot3` (`keyboard_control.py` 예시 기준). 실제와 다르면 알려만 주세요. 관제는 설정 한 줄만 바꿉니다 |
| 범퍼 | 관제가 Create 3의 `/<ns>/hazard_detection`을 직접 구독합니다 |
| 도킹 여부 | 관제가 `/<ns>/dock_status`를 직접 구독합니다 |
| 위치 | 관제가 `/<ns>/amcl_pose`를 직접 구독합니다 (STATE의 pose는 보조) |
| `PAUSED`에서 새 목적지 | `MOVE`가 거절되는 것을 알고 있으니, 관제가 `CANCEL` → `MOVE` 순서로 보냅니다 |

## 필수 변경 4건

### R1. 명령 거절 이벤트 + 관제 임무 ID (`command_receiver.py`)

**왜**: 지금은 `MOVE`·`PAUSE`·`RESUME`·`CANCEL`이 거절되면 로그만 남아서 관제가 모릅니다. 또 관제 임무와 로봇 이벤트를 정확히 짝지을 ID가 없습니다.

**바꿀 것**
- 명령 끝에 `id=<관제 ID>`를 붙일 수 있게 합니다 (선택). 예: `MOVE F 3 id=L-0007-2`
- `MOVE`에 붙은 ID는 `mission.start(..., mission_id=...)`로 넘깁니다. `start()`가 이미 이 인자를 받으므로 MissionManager는 고칠 필요가 없습니다.
- 거절되면 status에 `COMMAND_REJECTED` 이벤트를 보냅니다.

`parse_command`, `execute`, `CommandReceiver.on_command`를 아래로 바꿔 주세요. `keyboard_control.py`는 `parse_command`를 검사용으로만 부르므로 그대로 동작합니다.

```python
def parse_command(text):
    """'MOVE F 3 id=L-0007-2' -> ('MOVE', 'F', 3, 'L-0007-2'). id=는 선택."""
    words = text.strip().split()
    mission_id = None
    if words and words[-1].lower().startswith("id="):
        mission_id = words.pop()[3:] or None
    words = [w.upper() for w in words]
    if len(words) == 1 and words[0] in CONTROL:
        return words[0], None, None, mission_id
    if words and words[0] == "MOVE":
        words = words[1:]
    if len(words) not in (1, 2) or words[0] not in set("ABCDEF"):
        raise ValueError("A~F, MOVE F [1~3] [id=...], PAUSE, RESUME, CANCEL 중 하나")
    if len(words) == 2 and words[1] not in ("1", "2", "3"):
        raise ValueError("응급도는 1~3 정수")
    return "MOVE", words[0], int(words[1]) if len(words) == 2 else 1, mission_id


def execute(mission, text):
    """Parse and run one command. Returns (action, accepted, request_id)."""
    action, room, priority, request_id = parse_command(text)
    if action == "MOVE":
        return action, mission.start(room, priority=priority, mission_id=request_id), request_id
    ok = {"PAUSE": lambda: mission.pause("관제 요청"),
          "RESUME": mission.resume,
          "CANCEL": mission.cancel_mission}[action]()
    return action, ok, request_id
```

```python
    def on_command(self, msg):
        text = msg.data.strip()
        try:
            action, ok, request_id = execute(self.mission, text)
        except ValueError as exc:
            self.mission.emit("COMMAND_REJECTED", command=text, reason=str(exc),
                              request_id=None, state=self.mission.snapshot()["state"])
            self.get_logger().warning(f"관제 명령 무시 ({text!r}): {exc}")
            return
        if not ok:
            self.mission.emit("COMMAND_REJECTED", command=text, reason=f"{action} 불가 상태",
                              request_id=request_id, state=self.mission.snapshot()["state"])
        self.get_logger().info(f"{text}: {'ACCEPTED' if ok else 'REJECTED'}")
```

**확인 방법**
```bash
ros2 topic pub --once /robot2/hospital_nav/command std_msgs/msg/String "{data: 'MOVE Z 1'}"
ros2 topic echo /robot2/hospital_nav/status   # COMMAND_REJECTED 가 보여야 함
ros2 topic pub --once /robot2/hospital_nav/command std_msgs/msg/String "{data: 'MOVE F 3 id=T-1'}"
# MISSION_ACCEPTED 의 mission_id 가 T-1 이어야 함
```

### R2. `dock_manager.py` 소스 커밋, `.pyc` 삭제

**왜**: `hospital_nav/__pycache__/dock_manager.cpython-312.pyc`만 올라와 있고 `dock_manager.py`가 없습니다. 다른 사람은 이 코드를 읽거나 고칠 수 없습니다. `.gitignore`에 이미 `__pycache__/`가 있는데 웹 업로드라 같이 올라갔습니다.

```bash
git rm -r --cached src/part4_amr_nav/hospital_nav/__pycache__
git add src/part4_amr_nav/hospital_nav/dock_manager.py
git commit -m "part4: add dock_manager source, drop pycache"
```

### R3. 언도킹 연동 + STATE에 `docked` (`mission_manager.py`, `command_receiver.py`)

**왜**: 로봇은 도크에서 시작합니다. 지금 `MOVE`는 도킹 상태를 보지 않고 바로 Nav2 goal을 보냅니다. `DockManager`는 있지만 MissionManager에 연결되어 있지 않습니다.

**바꿀 것** (`.pyc`에서 읽은 `DockManager` API 기준: `DockManager(namespace)`, `undock()`, `dock()`, `cancel()`, `state()` → `IDLE/RUNNING/SUCCEEDED/FAILED/CANCELED`, `is_docked`)
- `MissionManager.__init__`: `self.dock = DockManager("/" + self.robot)`
- `command_receiver.run()`: 실행기에 `mission.dock`도 추가 (`nodes = (mission, mission.nav, mission.dock, *extra_nodes)`)
- `start()`: 검증이 끝난 뒤 `self.dock.is_docked`면 `self.state = "UNDOCKING"`, `emit("UNDOCKING")`, `self.dock.undock()` 후 `True`를 반환합니다. 아니면 지금처럼 `self._go()`.
- `_tick()`: `state == "UNDOCKING"`일 때
  - `dock.state() == "SUCCEEDED"` → `emit("UNDOCKED")` → `self._go()`
  - `FAILED`·`CANCELED` → `self.reason = "언도킹 실패"` → `self._finish("FAILED")`
- `cancel_mission()`: `UNDOCKING`이면 `self.dock.cancel()` 후 `self._finish("CANCELED")`
- `snapshot()`: `"docked": self.dock.is_docked`를 추가합니다.

**확인 방법**: 도크에 있는 로봇에 `MOVE D 1` → status에 `UNDOCKING` → `UNDOCKED` → `NAVIGATING` 순서로 오고, STATE의 `docked`가 `true` → `false`로 바뀌어야 합니다.

### R4. `ros2 run`이 되게 (`setup.py`, `package.xml`)

**왜**: `entry_points`가 비어 있어서 `ros2 run`으로 띄울 수 없습니다. 관제는 통합 시험 때 PART 04 노드를 `ros2 run`으로 띄웁니다.

```python
entry_points={'console_scripts': [
    'command_receiver = hospital_nav.command_receiver:main',
    'keyboard_control = hospital_nav.keyboard_control:main',
]},
```

`package.xml`에 실제로 쓰는 의존성을 추가해 주세요: `geometry_msgs`, `nav2_msgs`, `action_msgs`, `irobot_create_msgs`.

실행: `ros2 run part4_amr_nav command_receiver --robot robot2`

## 나중에 (이번 주 필수 아님)

- `DOCK` 명령: 도킹 위치 앞 구역으로 이동한 뒤 `dock()` 호출. 관제는 임무 후 도킹 복귀를 꺼 두었다가 이것이 생기면 켭니다.
- 통로 입구 대기점·대피 지점을 구역 이름처럼 받기 (예: `MOVE T`). 생기면 관제가 통로 앞 대기와 응급 대피를 씁니다.
