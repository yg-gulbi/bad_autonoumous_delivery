"""목적: ``tts_speaker.py``의 명령 탐색·비동기 반복·발화 완료 보장·예외 격리를 검증한다."""

import threading
import time

import pytest

from part5_bed_exit.tts_speaker import (
    DEFAULT_TTS_MESSAGE,
    TtsSpeaker,
    resolve_tts_command,
)


class _BlockingProc:
    """실제 오디오 장치 없이 발화 진행·완료·종료 시점을 정밀 제어하는 Mock Popen."""

    def __init__(self, argv, *, returncode=0, auto_finish=False):
        self.argv = argv
        self.returncode = returncode
        self.started = threading.Event()
        self.allow_finish = threading.Event()
        self.finished = threading.Event()
        self.terminated = False
        self.killed = False
        if auto_finish:
            self.allow_finish.set()

    def wait(self, timeout=None):
        self.started.set()
        if not self.allow_finish.wait(timeout=timeout):
            raise TimeoutError('mock proc did not finish in time')
        self.finished.set()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.allow_finish.set()

    def kill(self):
        self.killed = True
        self.allow_finish.set()


def test_resolve_tts_command_priority_and_custom_template():
    assert resolve_tts_command(
        DEFAULT_TTS_MESSAGE,
        language='ko',
        which=lambda name: f'/usr/bin/{name}' if name in ('spd-say', 'espeak-ng') else None,
    ) == ['/usr/bin/spd-say', '-l', 'ko', '-w', DEFAULT_TTS_MESSAGE]

    assert resolve_tts_command(
        '경고',
        language='ko',
        which=lambda name: '/usr/bin/espeak-ng' if name == 'espeak-ng' else None,
    ) == ['/usr/bin/espeak-ng', '-v', 'ko', '경고']

    assert resolve_tts_command(
        '경고',
        language='ko',
        which=lambda name: '/usr/bin/espeak' if name == 'espeak' else None,
    ) == ['/usr/bin/espeak', '-v', 'ko', '경고']

    assert resolve_tts_command('경고', which=lambda name: None) is None

    # 사용자 커맨드: {language}/{message} 치환 및 미포함 시 메시지 자동 추가
    assert resolve_tts_command(
        '이탈',
        language='ko',
        command='spd-say -l {language} -r -10 -w {message}',
    ) == ['spd-say', '-l', 'ko', '-r', '-10', '-w', '이탈']

    assert resolve_tts_command(
        '이탈',
        language='ko',
        command='my-tts --fast',
    ) == ['my-tts', '--fast', '이탈']


def test_tts_speaker_repeats_async_and_finishes_current_utterance_without_cutoff():
    procs: list[_BlockingProc] = []
    spawn_events = [threading.Event(), threading.Event()]

    def fake_popen(argv, **kwargs):
        idx = len(procs)
        proc = _BlockingProc(argv)
        procs.append(proc)
        if idx < len(spawn_events):
            spawn_events[idx].set()
        return proc

    speaker = TtsSpeaker(
        DEFAULT_TTS_MESSAGE,
        enabled=True,
        repeat_interval=0.02,
        language='ko',
        which=lambda name: '/usr/bin/spd-say' if name == 'spd-say' else None,
        popen_factory=fake_popen,
    )
    try:
        # 1) set_active(True)는 블로킹 없이 즉시 반환되어야 한다.
        t0 = time.monotonic()
        speaker.set_active(True)
        assert (time.monotonic() - t0) < 0.1

        assert spawn_events[0].wait(timeout=2.0)
        assert procs[0].started.wait(timeout=2.0)
        assert procs[0].argv == ['/usr/bin/spd-say', '-l', 'ko', '-w', DEFAULT_TTS_MESSAGE]
        assert speaker.is_speaking is True

        # 2) 1회 발화 완료 -> repeat_interval 후 자동으로 2회차 반복 시작
        procs[0].allow_finish.set()
        assert procs[0].finished.wait(timeout=2.0)
        assert spawn_events[1].wait(timeout=2.0)
        assert procs[1].started.wait(timeout=2.0)

        # 3) 2회차 발화 도중 정상 복귀(set_active(False)) 호출:
        #    진행 중인 2회차 발화는 중간에 끊기지 않고(terminated=False) 끝까지 재생되어야 한다.
        speaker.set_active(False)
        time.sleep(0.05)
        assert procs[1].terminated is False
        assert procs[1].killed is False
        assert speaker.is_speaking is True

        # 4) 2회차 발화가 자연 종료된 뒤에는 3회차가 시작되지 않아야 한다(무한 반복 방지).
        procs[1].allow_finish.set()
        assert procs[1].finished.wait(timeout=2.0)
        time.sleep(0.1)
        assert len(procs) == 2
        assert speaker.is_speaking is False
        assert speaker.is_active is False
    finally:
        speaker.close()


def test_tts_speaker_close_terminates_in_progress_utterance():
    proc_holder = []
    started = threading.Event()

    def fake_popen(argv, **kwargs):
        proc = _BlockingProc(argv)
        proc_holder.append(proc)
        started.set()
        return proc

    speaker = TtsSpeaker(
        DEFAULT_TTS_MESSAGE,
        repeat_interval=0.05,
        which=lambda name: '/usr/bin/spd-say' if name == 'spd-say' else None,
        popen_factory=fake_popen,
    )
    speaker.set_active(True)
    assert started.wait(timeout=2.0)
    assert proc_holder[0].started.wait(timeout=2.0)

    speaker.close()
    assert proc_holder[0].terminated is True


def test_tts_speaker_isolates_missing_backend_and_nonzero_exit():
    errors = []
    no_bin = TtsSpeaker(
        DEFAULT_TTS_MESSAGE,
        which=lambda name: None,
        on_error=errors.append,
    )
    try:
        no_bin.set_active(True)
        deadline = time.monotonic() + 2.0
        while no_bin.enabled and time.monotonic() < deadline:
            time.sleep(0.01)
        assert no_bin.enabled is False
        assert any('No TTS backend found' in msg for msg in errors)
    finally:
        no_bin.close()

    fail_errors = []
    fail_speaker = TtsSpeaker(
        DEFAULT_TTS_MESSAGE,
        which=lambda name: '/usr/bin/spd-say' if name == 'spd-say' else None,
        popen_factory=lambda argv, **kwargs: _BlockingProc(argv, returncode=1, auto_finish=True),
        on_error=fail_errors.append,
    )
    try:
        fail_speaker.set_active(True)
        deadline = time.monotonic() + 2.0
        while fail_speaker.enabled and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fail_speaker.enabled is False
        assert any('exited with status 1' in msg for msg in fail_errors)
    finally:
        fail_speaker.close()


def test_tts_speaker_validates_configuration():
    with pytest.raises(TypeError, match='tts_enabled'):
        TtsSpeaker(enabled=1)
    with pytest.raises(ValueError, match='tts_message'):
        TtsSpeaker('', enabled=True)
    with pytest.raises(ValueError, match='tts_repeat_interval'):
        TtsSpeaker(repeat_interval=-0.5)
    with pytest.raises(ValueError):
        TtsSpeaker(command='unterminated "quote')

    # disabled일 때는 빈 메시지·호출도 안전하게 무시한다.
    disabled = TtsSpeaker('', enabled=False)
    disabled.set_active(True)
    assert disabled.is_active is False
    disabled.close()


def test_resolve_tts_command_prefers_bundled_or_custom_audio_file_with_ffplay(tmp_path):
    custom_mp3 = tmp_path / 'alert.mp3'
    custom_mp3.write_bytes(b'ID3')

    assert resolve_tts_command(
        DEFAULT_TTS_MESSAGE,
        language='ko',
        which=lambda name: f'/usr/bin/{name}' if name in ('ffplay', 'spd-say') else None,
        audio_file=custom_mp3,
    ) == ['/usr/bin/ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', str(custom_mp3)]

    # 기본 문구일 때는 내장 한국어 여성 음성 MP3가 있으면 ffplay로 우선 재생
    bundled_cmd = resolve_tts_command(
        DEFAULT_TTS_MESSAGE,
        language='ko',
        which=lambda name: f'/usr/bin/{name}' if name in ('ffplay', 'spd-say') else None,
    )
    assert bundled_cmd[0] == '/usr/bin/ffplay'
    assert bundled_cmd[-1].endswith('tts_bed_exit_ko.mp3')

    # 문구를 바꿨을 때는 기본 MP3 대신 spd-say로 폴백
    assert resolve_tts_command(
        '사용자 지정 문구',
        language='ko',
        which=lambda name: f'/usr/bin/{name}' if name in ('ffplay', 'spd-say') else None,
    ) == ['/usr/bin/spd-say', '-l', 'ko', '-w', '사용자 지정 문구']
