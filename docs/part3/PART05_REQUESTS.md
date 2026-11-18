# PART 05(침상 이탈 감지)에 요구하는 변경

기준 코드: `part5-refactoring` (64e57ce, 11/18 14:47) `src/part5_bed_exit/part5_bed_exit/bed_exit_node.py`, `bed_exit_state.py`. 그 전 `feat/part5-imsohy`(0dc450f)도 같은 문제가 있다. 리팩터링 뒤에도 `process_frame()`의 `self.publisher.publish(Bool(data=unsafe))` 앞에 같은 확인이 없다.

## 관제가 이 토픽을 어떻게 쓰나

- `/patient/bed_exit` (`std_msgs/Bool`)가 `true`가 되면 관제는 Robot A를 세운다.
- 이 토픽이 1초 넘게 안 오면 경고하고, 3초 넘게 안 오면 **침상 감시가 끊긴 것으로 보고 Robot A를 세운다.** 사람이 웹캠을 확인하고 재개해야 다시 움직인다.
- 그래서 관제는 "메시지가 온다 = 감시가 살아 있다"로 믿는다. 판정을 못 하는 동안에는 메시지가 오면 안 된다.

지금 코드는 매 프레임(30 Hz) 발행하고, 카메라·판정 실패 프레임은 발행하지 않는다. 이 부분은 그대로 좋다.

## P5-R1. 5프레임이 모이기 전에는 발행하지 않기

**문제**

- `BedExitState.update()`는 이력이 5개 미만이면 `False`를 돌려준다. `process_frame()`은 그 값을 그대로 발행한다.
- 이력은 노드 시작 직후, 그리고 카메라 읽기나 판정이 실패할 때마다 비워진다.
- 그래서 실패 직후 4프레임 동안은 판정 없이 `False`(정상)가 나간다.
- 카메라가 몇 프레임마다 한 번씩 실패하면 이력이 5개까지 차지 못하고 계속 비워진다. 그러면 환자가 침대를 떠나도 `False`만 계속 나가고, 관제는 감시가 정상이라고 믿는다.
- "판정 불가일 때 거짓 정상을 발행하지 않는다"는 PART 05 원칙과도 어긋난다.

**변경** (`bed_exit_node.py`의 `process_frame`, 두 줄)

```python
        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            ...
            return
        if len(self.state.history) < 5:
            # 5프레임이 모이기 전에는 정상/이탈 어느 쪽도 발행하지 않는다 (거짓 정상 방지)
            self._render_preview_if_any(frame, None)
            return
        self.publisher.publish(Bool(data=unsafe))
```

- 프리뷰 처리는 지금 코드 방식(`getattr(self, '_render_preview', None)`)을 그대로 써도 된다. 위 `_render_preview_if_any`는 그 자리를 뜻한다.
- `bed_exit_state.py`의 5프레임 다수결 규칙(5개 중 3개 이상 `True`)은 바꾸지 않는다.

**확인 방법**

- 테스트: 가짜 판정기로 `True, 실패, True, True, 실패, True, ...`처럼 3프레임마다 실패시키면 아무것도 발행되지 않아야 한다. 지금 코드에서는 `False`가 발행된다.
- 실행: 노드를 켜고 `ros2 topic hz /patient/bed_exit`를 본다. 처음 5프레임(30 Hz에서 약 0.17초) 뒤부터 30 Hz로 나오면 된다. 웹캠을 뽑으면 발행이 멈춰야 한다.

**관제 쪽 영향**

- 반영 전에도 관제의 끊김 감시는 동작한다. 다만 위의 "간헐 실패 중 거짓 정상"은 관제가 알아챌 방법이 없다.
- 반영 후에는 간헐 실패가 발행 공백으로 나타나서, 관제의 끊김 감시가 잡는다.
