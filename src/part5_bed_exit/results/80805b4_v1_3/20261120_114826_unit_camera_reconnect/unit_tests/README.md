# 단위 테스트 결과 (v1.3.4 웹캠 미연결 대기 · USB 자동 재연결)

## 실행 결과

| 항목 | 결과 |
| --- | --- |
| 기준선 | `80805b4` (`v1.3.4` patch — 웹캠 미연결 대기 및 재연결 보강) |
| 실행 시각 | 2026-11-20 11:48:48 KST |
| 실행 명령 | `PART5_PYTHON=/usr/bin/python3 bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit` |
| 전체 | 51 |
| 통과 | 51 |
| 실패 | 0 |
| 오류 | 0 |
| 건너뜀 | 0 |
| 실행 시간 | 0.73초 |

## 생산 코드별 결과

| 생산 코드 | 테스트 수 | 결과 |
| --- | ---: | --- |
| `methods/bed_exit_core.py` | 8 | 통과 |
| `bed_exit_method.py` | 13 | 통과 |
| `bed_exit_node.py` | 17 | 통과 |
| `bed_exit_state.py` | 6 | 통과 |
| `pretrained.py` | 4 | 통과 |
| `roi_labeler.py` | 3 | 통과 |

## 이번 커밋 신규 추가 단위 테스트 (`test_bed_exit_node.py`, 5건)

1. `test_init_stays_alive_when_webcam_unavailable`: 시작 시 웹캠이 미연결(`isOpened() == False`)이어도 예외 없이 노드와 타이머를 유지하고 `capture is None`으로 대기하는지 검증
2. `test_init_still_raises_when_video_file_missing`: MP4(`video_path`) 파일 열기 실패 시 기존대로 `RuntimeError`를 발생시키고 detector를 정리하는지 검증
3. `test_init_rejects_invalid_reconnect_interval`: `reconnect_interval <= 0` 전달 시 `ValueError`를 발생시키는지 검증
4. `test_process_frame_reconnects_after_cooldown`: `reconnect_interval` 경과 전에는 `VideoCapture` 재호출을 건너뛰고, 쿨다운 이후 연결 성공 시 즉시 프레임 처리·발행을 재개하는지 검증
5. `test_process_frame_releases_and_reconnects_on_usb_unplug_replug`: 실행 중 USB 분리(`read()` 실패) 시 즉시 `release()` 호출·`history` 초기화·`previous` 유지·미발행 후, 쿨다운 경과 뒤 재연결 시 5프레임 재워밍업을 거쳐 정상 발행을 복구하는지 검증

상세 테스트별 실행 시간과 XML 리포트는 같은 폴더의 `pytest.xml`, 전체 로그는 상위 폴더의 `execution.log` 및 `summary.json`에 저장되어 있다.
