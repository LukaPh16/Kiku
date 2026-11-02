#!/bin/sh
# Runs itself with the project venv (works from any directory).
''''exec "$(dirname "$0")/venv/bin/python" "$0" "$@" # '''
"""Start Kiku: shows the face overlay until you quit (Ctrl+C).

  ./kiku.py

Kiku is hidden until this runs. When this process exits (even if it crashes)
kiku-shell hides the overlay on its own.
"""
import signal
import threading

from ui.overlay import Overlay

stop = threading.Event()


def log(msg):
    print(f"[KIKU] {msg}", flush=True)


def on_hotkey(name):
    log(f"hotkey: {name}")   # panic is handled in the shell (aborts any motion)


def main():
    ov = Overlay()
    if not ov.available:
        log("kiku-shell is not running (enable it and log out/in); nothing to show")
        ov.close()
        return 1
    ov.on_hotkey(on_hotkey)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    ov.show()
    ov.set_state("idle")
    log("Kiku is up. Ctrl+C to quit.")
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    ov.hide()
    ov.close()
    log("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
