"""Speech to text with faster-whisper (loaded once, warmed up at startup)."""
import time

import numpy as np

import config


def _log(msg):
    print(f"[KIKU] stt: {msg}", flush=True)


class Stt:
    def __init__(self):
        from faster_whisper import WhisperModel
        t = time.monotonic()
        self.model = WhisperModel(config.STT_MODEL, device=config.STT_DEVICE,
                                  compute_type=config.STT_COMPUTE_TYPE)
        _log(f"loaded {config.STT_MODEL} in {(time.monotonic() - t) * 1000:.0f} ms")
        t = time.monotonic()
        self.transcribe(np.zeros(config.SAMPLE_RATE, np.float32), quiet=True)
        _log(f"warm-up {(time.monotonic() - t) * 1000:.0f} ms")

    def transcribe(self, audio, quiet=False):
        """float32 mono audio at SAMPLE_RATE -> text ("" if nothing)."""
        t = time.monotonic()
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1,
                                            initial_prompt=config.STT_PROMPT,
                                            condition_on_previous_text=False,
                                            without_timestamps=True, vad_filter=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        if not quiet:
            _log(f"{(time.monotonic() - t) * 1000:.0f} ms: {text!r}")
        return text
