"""Text to speech with Kyutai Pocket TTS, streamed to the speaker from a worker thread.

speak(text) blocks until playback ends; stop() (from any thread) cuts it off within one
audio block (~20 ms) and stops generation.
"""
import collections
import queue
import threading
import time

import config
import perf

_BLOCK = 512   # samples per output callback


def _log(msg):
    print(f"[KIKU] tts: {msg}", flush=True)


class Tts:
    def __init__(self, play=True):
        from pocket_tts import TTSModel
        t = time.monotonic()
        self.model = TTSModel.load_model(language=config.TTS_LANGUAGE)
        self.voice = self.model.get_state_for_audio_prompt(config.TTS_VOICE)
        self.rate = self.model.sample_rate
        self.play = play
        self._stop = threading.Event()
        for _ in self.synth("Hi."):   # warm-up
            pass
        _log(f"loaded pocket-tts ({config.TTS_VOICE}) + warm-up in {(time.monotonic() - t) * 1000:.0f} ms")
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="kiku-tts", daemon=True)
        self._thread.start()

    def speak(self, text):
        """Say text; returns when done or stopped."""
        done = threading.Event()
        self._stop.clear()
        self._jobs.put((text, done))
        done.wait()

    def stop(self):
        self._stop.set()

    def close(self):
        """Stop speaking, close the audio stream and end the worker."""
        self._stop.set()
        self._jobs.put(None)
        self._thread.join(2)

    def _worker(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            text, done = job
            try:
                self._say(text)
            except Exception as e:
                _log(f"failed: {e!r}")
            done.set()

    def synth(self, text):
        """Yield float32 numpy chunks as they are generated, until stopped. Not thread-safe."""
        for chunk in self.model.generate_audio_stream(self.voice, text, stop=self._stop):
            if self._stop.is_set():
                return
            yield chunk.numpy()

    def _say(self, text):
        t0 = time.monotonic()
        if not self.play:
            n = 0
            for a in self.synth(text):
                if not n:
                    perf.add("tts to first audio", t0)
                n += len(a)
            _log(f"synth {n * 1000 // self.rate} ms audio in {(time.monotonic() - t0) * 1000:.0f} ms (muted)")
            return
        import sounddevice as sd
        pending = collections.deque()   # float32 arrays still to play
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

        stream = sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32", blocksize=_BLOCK,
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
        if state["first"]:
            perf.add("tts to first audio", t0, state["first"])
        _log(f"{'stopped' if self._stop.is_set() else 'done'}: first audio {first:.0f} ms, total {total:.0f} ms")
