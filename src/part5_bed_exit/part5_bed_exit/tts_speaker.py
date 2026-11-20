"""환자 침대 이탈 감지 시 비동기 반복 TTS 안내 음성을 재생하는 모듈.

주요 보장 사항:
- ROS 2 타이머 콜백(``process_frame``)을 절대 블로킹하지 않는다.
- 환자 이탈 상태(``active=True``) 동안 지정한 문구를 ``repeat_interval`` 간격으로 반복한다.
- 환자 상태가 정상(``active=False``)으로 복귀하거나 예외가 발생해 중단 요청이 들어와도
  **이미 시작된 1회 발화는 중간에 끊지 않고 끝까지 재생한 뒤** 반복을 멈춘다.
- 노드 종료(``close()``) 시에는 실행 중인 TTS 프로세스를 즉시 정리하고 백그라운드 스레드를 회수한다.
- 오디오 장치·바이너리 오류는 내부에서 격리해 ``/patient/bed_exit`` 발행을 방해하지 않는다.
"""

from __future__ import annotations

import math
import shlex
import shutil
import subprocess
import threading
import time
from typing import Callable, Sequence


DEFAULT_TTS_MESSAGE = '주행 중인 침상에서 이탈하지 말아주세요.'
DEFAULT_TTS_LANGUAGE = 'ko'
DEFAULT_TTS_REPEAT_INTERVAL = 1.0


def _build_custom_command(
    command: str | Sequence[str],
    message: str,
    language: str,
) -> list[str]:
    """사용자 지정 TTS 명령 문자열·리스트에서 최종 argv를 만든다."""
    if isinstance(command, str):
        tokens = shlex.split(command)
    else:
        tokens = [str(part) for part in command]
    if not tokens:
        raise ValueError('tts_command must not be empty when specified')

    has_message_placeholder = any('{message}' in token for token in tokens)
    resolved = [
        token.replace('{message}', message).replace('{language}', language)
        for token in tokens
    ]
    if not has_message_placeholder:
        resolved.append(message)
    return resolved


def resolve_tts_command(
    message: str,
    language: str = DEFAULT_TTS_LANGUAGE,
    command: str | Sequence[str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str] | None:
    """실행할 TTS 외부 명령 argv를 결정한다.

    우선순위:
    1. 명시적 ``command`` (문자열 또는 argv 시퀀스; ``{message}``/``{language}`` 치환 지원)
    2. ``spd-say -l <language> -w <message>`` (Ubuntu 기본 Speech Dispatcher, 블로킹 완료 대기)
    3. ``espeak-ng -v <language> <message>``
    4. ``espeak -v <language> <message>``
    """
    if command is not None and (not isinstance(command, str) or command.strip()):
        return _build_custom_command(command, message, language)

    lookup = shutil.which if which is None else which
    spd_say = lookup('spd-say')
    if spd_say:
        return [spd_say, '-l', language, '-w', message]

    espeak_ng = lookup('espeak-ng')
    if espeak_ng:
        return [espeak_ng, '-v', language, message]

    espeak = lookup('espeak')
    if espeak:
        return [espeak, '-v', language, message]

    return None


class TtsSpeaker:
    """상태 기반 비동기 반복 TTS 재생기.

    ``set_active(True)`` 호출 시 백그라운드 데몬 스레드가 1회 발화를 끝까지 재생하고
    ``repeat_interval``초 대기 후 여전히 활성 상태이면 다시 발화한다.
    ``set_active(False)`` 호출 시 현재 재생 중인 발화는 중단하지 않고 끝까지 마치게 둔 뒤
    추가 반복만 중단한다.
    """

    def __init__(
        self,
        message: str = DEFAULT_TTS_MESSAGE,
        *,
        enabled: bool = True,
        repeat_interval: float = DEFAULT_TTS_REPEAT_INTERVAL,
        language: str = DEFAULT_TTS_LANGUAGE,
        command: str | Sequence[str] | None = None,
        which: Callable[[str], str | None] | None = None,
        popen_factory: Callable[..., subprocess.Popen] | None = None,
        clock: Callable[[], float] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        if type(enabled) is not bool:
            raise TypeError('tts_enabled must be a Python bool')
        if not isinstance(message, str) or (enabled and not message.strip()):
            raise ValueError('tts_message must be a non-empty string when tts_enabled is True')
        if not isinstance(language, str) or (enabled and not language.strip()):
            raise ValueError('tts_language must be a non-empty string when tts_enabled is True')
        repeat_interval = float(repeat_interval)
        if not math.isfinite(repeat_interval) or repeat_interval < 0.0:
            raise ValueError('tts_repeat_interval must be >= 0')

        normalized_command: str | list[str] | None
        if command is None:
            normalized_command = None
        elif isinstance(command, str):
            stripped = command.strip()
            if stripped:
                # 잘못된 따옴표 구문은 실행 시점이 아니라 초기화 시점에 즉시 검출한다.
                shlex.split(stripped)
                normalized_command = stripped
            else:
                normalized_command = None
        else:
            normalized_command = [str(part) for part in command]
            if not normalized_command:
                normalized_command = None

        self.message = message
        self.enabled = enabled
        self.repeat_interval = repeat_interval
        self.language = language.strip() or DEFAULT_TTS_LANGUAGE
        self.command = normalized_command

        self._which = shutil.which if which is None else which
        self._popen_factory = subprocess.Popen if popen_factory is None else popen_factory
        self._clock = time.monotonic if clock is None else clock
        self._on_error = on_error

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._active = False
        self._speaking = False
        self._closed = False
        self._active_proc = None
        self._thread: threading.Thread | None = None

    @property
    def is_active(self) -> bool:
        """현재 반복 재생이 활성화되어 있는지 반환한다."""
        with self._lock:
            return self._active

    @property
    def is_speaking(self) -> bool:
        """현재 TTS 프로세스가 발화를 재생 중인지 반환한다."""
        with self._lock:
            return self._speaking

    def set_active(self, active: bool) -> None:
        """비동기로 반복 TTS 활성 상태를 전환한다.

        ``active=False``를 전달해도 진행 중인 발화 프로세스는 종료하지 않으며,
        현재 1회 발화가 자연스럽게 끝난 뒤 반복만 중단한다.
        """
        desired = bool(active)
        with self._cond:
            if self._closed or not self.enabled:
                self._active = False
                self._cond.notify_all()
                return
            self._active = desired
            if desired and self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker_loop,
                    name='part5-tts-speaker',
                    daemon=True,
                )
                self._thread.start()
            self._cond.notify_all()

    def close(self) -> None:
        """노드 종료 시 실행 중인 TTS 프로세스와 워커 스레드를 안전하게 정리한다."""
        with self._cond:
            self._closed = True
            self._active = False
            proc = self._active_proc
            thread = self._thread
            self._cond.notify_all()

        if proc is not None:
            self._terminate_process(proc)
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)

    def _report_error(self, detail: str) -> None:
        if callable(self._on_error):
            try:
                self._on_error(detail)
            except Exception:
                pass

    @staticmethod
    def _terminate_process(proc) -> None:
        terminate = getattr(proc, 'terminate', None)
        wait = getattr(proc, 'wait', None)
        kill = getattr(proc, 'kill', None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass
        if callable(wait):
            try:
                wait(timeout=1.0)
                return
            except Exception:
                pass
        if callable(kill):
            try:
                kill()
            except Exception:
                pass

    def _speak_once(self) -> bool:
        """TTS 명령을 1회 실행해 발화가 끝날 때까지 대기한다."""
        try:
            argv = resolve_tts_command(
                self.message,
                language=self.language,
                command=self.command,
                which=self._which,
            )
            if not argv:
                raise FileNotFoundError(
                    'No TTS backend found (install speech-dispatcher/spd-say or espeak-ng, '
                    'or set tts_command)'
                )
            proc = self._popen_factory(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self._cond:
                if self._closed:
                    should_terminate = True
                else:
                    self._active_proc = proc
                    should_terminate = False
            if should_terminate:
                self._terminate_process(proc)
                return True

            returncode = proc.wait()
            with self._cond:
                was_closed = self._closed
                if self._active_proc is proc:
                    self._active_proc = None
            if was_closed:
                return True
            if returncode != 0:
                raise RuntimeError(
                    f'TTS command exited with status {returncode}: {argv[0]}'
                )
            return True
        except Exception as exc:
            self._report_error(f'TTS disabled after playback failure: {exc}')
            return False

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while not self._closed and not self._active:
                    self._cond.wait()
                if self._closed:
                    return
                if not self.enabled:
                    self._active = False
                    continue
                self._speaking = True

            ok = self._speak_once()

            with self._cond:
                self._speaking = False
                self._active_proc = None
                if not ok:
                    self.enabled = False
                    self._active = False
                    continue
                if self._closed:
                    return
                if not self._active:
                    # 발화 도중 정상 복귀 또는 예외 발생: 1회 발화는 완료했고 추가 반복은 중단한다.
                    continue
                if self.repeat_interval > 0.0:
                    deadline = self._clock() + self.repeat_interval
                    while not self._closed and self._active:
                        remaining = deadline - self._clock()
                        if remaining <= 0.0:
                            break
                        self._cond.wait(timeout=remaining)
