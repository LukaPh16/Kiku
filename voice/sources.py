"""Swappable input/output. Core logic only sees InputSource/OutputSource.

InputSource.listen() returns the next command meant for Kiku (wake word already removed),
"" for "heard nothing for me", or None when the source is closed.
OutputSource.say(text) blocks until spoken; stop() cuts it off from any thread.
"""
import sys
import threading
import time

import config
import perf
from voice.wake import strip_wake


def _log(msg):
    print(f"[KIKU] {msg}", flush=True)


class InputSource:
    def __init__(self, on_state=None):
        self.on_state = on_state or (lambda state: None)   # "listen" | "think" | "idle"

    def listen(self):
        raise NotImplementedError

    def push_to_talk(self):
        """PTT hotkey pressed: accept the next utterance without a wake word."""

    def close(self):
        pass


class OutputSource:
    def say(self, text):
        raise NotImplementedError

    def stop(self):
        pass

    def close(self):
        pass


class LocalMic(InputSource):
    """Laptop mic -> VAD -> Whisper -> wake word / PTT check."""

    def __init__(self, on_state=None):
        super().__init__(on_state)
        from voice.mic import Mic
        from voice.stt import Stt
        self.stt = Stt()
        self.mic = Mic()
        self._ptt_until = 0.0
        self._closed = threading.Event()

    def push_to_talk(self):
        self._ptt_until = time.monotonic() + config.PTT_WINDOW_S
        self.on_state("listen")
        _log(f"push-to-talk: listening for {config.PTT_WINDOW_S:.0f} s")

    def _ptt_armed(self):
        return time.monotonic() < self._ptt_until

    def listen(self):
        if self._closed.is_set():
            return None
        audio = self.mic.listen(on_start=lambda: self.on_state("listen"), should_stop=self._should_stop)
        if audio is None:
            if self._closed.is_set():
                return None
            self._ptt_until = 0.0
            _log("push-to-talk: timed out")
            self.on_state("idle")
            return ""
        if len(audio) == 0:
            self.on_state("listen" if self._ptt_armed() else "idle")
            return ""
        ptt = self._ptt_until > 0   # pressed before or while speaking; one utterance uses it up
        self._ptt_until = 0.0
        self.on_state("think")
        t = time.monotonic()
        text = self.stt.transcribe(audio)
        perf.add("stt", t)
        command = strip_wake(text)
        if command is None and ptt:
            command = text.strip(" .")
        if command is None:
            _log(f"no wake word, ignored: {text!r}")
            self.on_state("idle")
            return ""
        if not command:
            self.push_to_talk()   # just "Kiku": the next utterance is the command
            return ""
        return command

    def _should_stop(self):
        # Stop waiting when closed, or when a PTT window ran out with no speech.
        if self._closed.is_set():
            return True
        return self._ptt_until > 0 and not self._ptt_armed() and not self.mic.vad.speaking

    def close(self):
        self._closed.set()
        self.mic.close()


class TextConsole(InputSource):
    """Type commands in the terminal (no mic needed). No wake word required."""

    def listen(self):
        try:
            text = input("you> ")
        except EOFError:
            return None
        self.on_state("think")
        command = strip_wake(text)
        return (command if command is not None else text).strip()


class LocalSpeaker(OutputSource):
    def __init__(self, play=True):
        from voice.tts import Tts
        self.tts = Tts(play=play)

    def say(self, text):
        print(f"kiku> {text}", flush=True)
        self.tts.speak(text)

    def stop(self):
        self.tts.stop()

    def close(self):
        self.tts.close()


class PrintOutput(OutputSource):
    """Just prints the reply (no TTS)."""

    def say(self, text):
        print(f"kiku> {text}", file=sys.stdout, flush=True)
