"""Text to speech with Piper, played on a worker thread.

speak(text) blocks until playback ends; stop() (from any thread) cuts it off within one
audio block (~20 ms).
"""
import collections
import queue
import threading
import time

import numpy as np

import config

_BLOCK = 512   # samples per output callback


def _log(msg):
    print(f"[KIKU] tts: {msg}", flush=True)


class Tts:
    def __init__(self, play=True):
        from piper import PiperVoice
        t = time.monotonic()
        self.voice = PiperVoice.load(str(config.PIPER_VOICE))
        self.rate = self.voice.config.sample_rate
        self.play = play
        for _ in self.voice.synthesize("Hi."):   # warm-up
            pass
        _log(f"loaded {config.PIPER_VOICE.name} + warm-up in {(time.monotonic() - t) * 1000:.0f} ms")
        self._jobs = queue.Queue()
        self._stop = threading.Event()
        threading.Thread(target=self._worker, name="kiku-tts", daemon=True).start()

    def speak(self, text):
        """Say text; returns when done or stopped."""
        done = threading.Event()
        self._stop.clear()
        self._jobs.put((text, done))
        done.wait()

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def _worker(self):
        while True:
            text, done = self._jobs.get()
            try:
                self._say(text)
            except Exception as e:
                _log(f"failed: {e!r}")
            done.set()

    def synth(self, text):
        """Yield int16 chunks (one per sentence) until stopped."""
        for chunk in self.voice.synthesize(text):
            if self._stop.is_set():
                return
            yield chunk.audio_int16_array

    def _say(self, text):
        t0 = time.monotonic()
        if not self.play:
            n = sum(len(a) for a in self.synth(text))
            _log(f"synth {n * 1000 // self.rate} ms audio in {(time.monotonic() - t0) * 1000:.0f} ms (muted)")
            return
        import sounddevice as sd
        pending = collections.deque()   # int16 arrays still to play
        state = {"synth_done": False, "first": None}
        finished = threading.Event()

        def callback(outdata, frames, t, status):
            if self._stop.is_set():
                raise sd.CallbackAbort
            out = outdata[:, 0]
            filled = 0
            while filled < frames and pending:
                a = pending[0]
                n = min(frames - filled, len(a))
                out[filled:filled + n] = a[:n]
                filled += n
                if n == len(a):
                    pending.popleft()
                else:
                    pending[0] = a[n:]
            out[filled:] = 0
            if filled and state["first"] is None:
                state["first"] = time.monotonic()
            if not pending and state["synth_done"]:
                raise sd.CallbackStop

        stream = sd.OutputStream(samplerate=self.rate, channels=1, dtype="int16", blocksize=_BLOCK,
                                 device=config.SPEAKER_DEVICE, callback=callback,
                                 finished_callback=finished.set)
        with stream:
            for audio in self.synth(text):
                pending.append(audio)
            state["synth_done"] = True
            while not finished.wait(0.02):
                if self._stop.is_set():
                    break
        total = (time.monotonic() - t0) * 1000
        first = (state["first"] - t0) * 1000 if state["first"] else -1
        _log(f"{'stopped' if self._stop.is_set() else 'done'}: first audio {first:.0f} ms, total {total:.0f} ms")
