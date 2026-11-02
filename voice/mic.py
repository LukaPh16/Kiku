"""Microphone capture with a simple energy VAD.

An utterance starts after VAD_START_FRAMES loud frames in a row (loud = VAD_MARGIN_DB above
the noise floor measured at startup) and ends after VAD_SILENCE_MS of quiet, or at VAD_MAX_S.
Audio is float32 mono at SAMPLE_RATE, ready for Whisper.
"""
import collections
import queue
import time

import numpy as np

import config


def _log(msg):
    print(f"[KIKU] mic: {msg}", flush=True)


def frame_db(frame):
    """Loudness of a float32 frame in dBFS."""
    rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
    return 20.0 * np.log10(max(rms, 1e-10))


class Vad:
    """Frame-by-frame endpointing. feed() returns None, "start" or "end"."""

    def __init__(self, threshold_db):
        ms = config.VAD_FRAME_MS
        self.threshold_db = threshold_db
        self._preroll = collections.deque(maxlen=max(1, config.VAD_PREROLL_MS // ms))
        self._silence_max = max(1, config.VAD_SILENCE_MS // ms)
        self._max_frames = int(config.VAD_MAX_S * 1000 / ms)
        self.reset()

    def reset(self):
        self._preroll.clear()
        self.frames = []
        self.speaking = False
        self._loud_run = 0
        self._quiet_run = 0

    def feed(self, frame):
        loud = frame_db(frame) >= self.threshold_db
        if not self.speaking:
            self._preroll.append(frame)
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= config.VAD_START_FRAMES:
                self.speaking = True
                self.frames = list(self._preroll)
                self._quiet_run = 0
                return "start"
            return None
        self.frames.append(frame)
        self._quiet_run = 0 if loud else self._quiet_run + 1
        if self._quiet_run >= self._silence_max or len(self.frames) >= self._max_frames:
            return "end"
        return None

    def audio(self):
        """The utterance without the trailing silence."""
        keep = len(self.frames) - min(self._quiet_run, len(self.frames))
        return np.concatenate(self.frames[:max(keep, 1)]) if self.frames else np.zeros(0, np.float32)


class Mic:
    def __init__(self):
        import sounddevice as sd   # needs libportaudio2
        self._q = queue.Queue()
        self._dc = None   # running DC offset of the mic signal
        self._skip = max(1, 300 // config.VAD_FRAME_MS)   # drop the click when the mic opens
        self._frame = int(config.SAMPLE_RATE * config.VAD_FRAME_MS / 1000)
        self._stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=1, dtype="float32",
                                      blocksize=self._frame, device=config.MIC_DEVICE,
                                      callback=self._on_audio)
        self._stream.start()
        self.vad = Vad(self.calibrate())

    def _on_audio(self, indata, frames, t, status):
        # Some mics sit at a constant offset instead of 0, which reads as loud noise. Track and remove it.
        if self._skip > 0:
            self._skip -= 1
            return
        x = indata[:, 0].copy()
        m = float(x.mean())
        self._dc = m if self._dc is None else self._dc + 0.05 * (m - self._dc)
        self._q.put(x - self._dc)

    def _flush(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def calibrate(self):
        """Measure the noise floor and return the speech threshold in dBFS."""
        self._flush()
        n = max(1, int(config.VAD_CALIBRATE_S * 1000 / config.VAD_FRAME_MS))
        dbs = [frame_db(self._q.get(timeout=2)) for _ in range(n)]
        floor = float(np.median(dbs))
        threshold = max(floor + config.VAD_MARGIN_DB, config.VAD_MIN_THRESHOLD_DB)
        if threshold > config.VAD_MAX_THRESHOLD_DB:
            _log(f"WARNING: noise floor {floor:.1f} dB is very loud (noisy room, talking during "
                 f"calibration, or mic gain too high); capping threshold")
            threshold = config.VAD_MAX_THRESHOLD_DB
        _log(f"noise floor {floor:.1f} dB, threshold {threshold:.1f} dB (DC offset {self._dc:+.3f})")
        return threshold

    def listen(self, on_start=None, should_stop=None):
        """Block until one utterance is captured; return float32 audio, or None if should_stop().

        on_start() is called at speech onset. Audio heard before this call (e.g. our own TTS)
        is discarded.
        """
        self._flush()
        self.vad.reset()
        t_wait = time.monotonic()
        t_start = None
        while True:
            if should_stop and should_stop():
                return None
            try:
                frame = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            ev = self.vad.feed(frame)
            if ev == "start":
                t_start = time.monotonic()
                if on_start:
                    on_start()
            elif ev == "end":
                audio = self.vad.audio()
                speech_ms = len(audio) * 1000 // config.SAMPLE_RATE
                waited = (t_start - t_wait) * 1000
                if speech_ms < config.VAD_MIN_SPEECH_MS:
                    _log(f"vad: dropped {speech_ms} ms blip")
                    return np.zeros(0, np.float32)
                _log(f"vad: utterance {speech_ms} ms (waited {waited:.0f} ms, "
                     f"endpoint {config.VAD_SILENCE_MS} ms)")
                return audio

    def close(self):
        self._stream.stop()
        self._stream.close()
