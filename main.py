#!/bin/sh
# Runs itself with the project venv (works from any directory).
''''exec "$(dirname "$0")/venv/bin/python" "$0" "$@" # '''
"""Kiku main loop: listen -> transcribe -> router (rules, else LLM) -> tool or chat -> speak.

  ./main.py            mic + speaker (say "Kiku, ..." or press Ctrl+Alt+Space first)
  ./main.py --text     type instead of speaking
  ./main.py --mute     print replies instead of speaking (still synthesizes, for timing)
  ./main.py --text --mute   quickest way to try the brain

Ctrl+Alt+K stops speech. Ctrl+C quits.
"""
import argparse
import signal

import time

from ui.overlay import Overlay


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

    from brain.llm import Llm
    from brain.router import Router
    from control.actions import Actions
    from control.locate import Locator
    from tools import open_app
    from voice.sources import LocalMic, LocalSpeaker, TextConsole
    actions = Actions(ov)
    open_app.init(ov, actions, Locator(ov))
    router = Router(Llm())
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
            t = time.monotonic()
            set_state("think")
            reply, route = router.handle(command)
            if route["tool"] == "stop":
                out.stop()
                actions.abort()
            if reply:
                set_state("talk")
                out.say(reply)
            log(f"turn: {route['via']} {route['tool']}, heard -> spoken end {(time.monotonic() - t) * 1000:.0f} ms")
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
