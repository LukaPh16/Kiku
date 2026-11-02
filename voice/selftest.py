"""Voice pipeline self-test without mic or speaker (no PortAudio needed).

Pocket TTS says a phrase -> resampled to 16 kHz, padded with noise -> energy VAD -> Whisper -> wake word.
  venv/bin/python -m voice.selftest
"""
import time

import numpy as np

import config
from voice.mic import Vad, frame_db
from voice.stt import Stt
from voice.tts import Tts
from voice.wake import strip_wake

PHRASES = {"Kiku, open Firefox.": "open firefox", "What time is it?": None}


def to_16k(x, rate):
    n = int(len(x) * config.SAMPLE_RATE / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def run_vad(audio, vad):
    frame = int(config.SAMPLE_RATE * config.VAD_FRAME_MS / 1000)
    events = []
    for i in range(0, len(audio) - frame + 1, frame):
        ev = vad.feed(audio[i:i + frame])
        if ev:
            events.append((ev, i / config.SAMPLE_RATE))
            if ev == "end":
                break
    return events


def main():
    rng = np.random.default_rng(0)
    tts = Tts(play=False)
    stt = Stt()
    noise = lambda s: (rng.standard_normal(int(s * config.SAMPLE_RATE)) * 0.002).astype(np.float32)
    calib = noise(config.VAD_CALIBRATE_S)
    frame = int(config.SAMPLE_RATE * config.VAD_FRAME_MS / 1000)
    floor = float(np.median([frame_db(calib[i:i + frame]) for i in range(0, len(calib) - frame + 1, frame)]))
    threshold = max(floor + config.VAD_MARGIN_DB, config.VAD_MIN_THRESHOLD_DB)
    print(f"[KIKU] selftest: noise floor {floor:.1f} dB, threshold {threshold:.1f} dB")
    failed = 0
    for phrase, want in PHRASES.items():
        t = time.monotonic()
        speech = np.concatenate(list(tts.synth(phrase)))
        print(f"[KIKU] selftest: tts {len(speech) * 1000 // tts.rate} ms audio in {(time.monotonic() - t) * 1000:.0f} ms")
        audio = np.concatenate([noise(1.0), to_16k(speech, tts.rate) + noise(len(speech) / tts.rate), noise(1.5)])
        vad = Vad(threshold)
        t = time.monotonic()
        events = run_vad(audio, vad)
        utt = vad.audio()
        print(f"[KIKU] selftest: vad {events} -> {len(utt) * 1000 // config.SAMPLE_RATE} ms "
              f"in {(time.monotonic() - t) * 1000:.1f} ms")
        text = stt.transcribe(utt)
        cmd = strip_wake(text)
        got = cmd.lower().strip(" .") if cmd is not None else None
        ok = [e for e, _ in events] == ["start", "end"] and got == want
        failed += not ok
        print(f"[KIKU] selftest: {'ok ' if ok else 'BAD'} {phrase!r} -> {text!r} -> command {cmd!r}")
    print(f"[KIKU] selftest: {'all passed' if not failed else f'{failed} failed'}")
    return failed != 0


if __name__ == "__main__":
    raise SystemExit(main())
