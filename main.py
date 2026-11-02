#!/bin/sh
# Runs itself with the project venv (works from any directory).
''''exec "$(dirname "$0")/venv/bin/python" "$0" "$@" # '''
"""Step 2 voice loop: listen -> transcribe -> echo "You said: ..." via TTS.

  ./main.py            mic + speaker (say "Kiku, ..." or press Ctrl+Alt+Space first)
  ./main.py --text     type instead of speaking
  ./main.py --mute     print replies instead of speaking (still synthesizes, for timing)

Ctrl+Alt+K stops speech. Ctrl+C quits.
"""
import argparse
import signal

from ui.overlay import Overlay
from voice.wake import is_stop


def log(msg):
    print(f"[KIKU] {msg}", flush=True)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="type commands instead of using the mic")
    ap.add_argument("--mute", action="store_true", help="don't play audio")
    args = ap.parse_args()

    ov = Overlay()
    if ov.available:
        ov.show()
        ov.set_state("idle")
        set_state = ov.set_state
    else:
        log("kiku-shell is not running; continuing without the overlay")
        set_state = lambda state: None

    from voice.sources import LocalMic, LocalSpeaker, TextConsole
    out = LocalSpeaker(play=not args.mute)
    src = TextConsole(set_state) if args.text else LocalMic(set_state)

    def on_hotkey(name):
        if name == "panic":
            log("panic: stopping speech")
            out.stop()
        elif name == "ptt":
            out.stop()   # talking over Kiku: cut it off and listen
            src.push_to_talk()

    ov.on_hotkey(on_hotkey)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    log("ready. Say \"Kiku, ...\"" + (" (or type)" if args.text else " or press the PTT hotkey"))

    try:
        while True:
            command = src.listen()
            if command is None:
                break
            if not command:
                continue
            if is_stop(command):
                log("voice stop")
                out.stop()
                set_state("idle")
                continue
            set_state("talk")
            out.say(f"You said: {command}")
            set_state("idle")
    except KeyboardInterrupt:
        out.stop()
    src.close()
    if ov.available:
        ov.hide()
    ov.close()
    log("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
