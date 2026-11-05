#!/bin/sh
# Runs itself with the project venv (works from any directory).
''''exec "$(dirname "$0")/venv/bin/python" "$0" "$@" # '''
"""Kiku main loop: listen -> transcribe -> router (rules, else LLM) -> tool or chat -> speak.

  ./main.py            mic + speaker (say "Kiku, ..." or press Ctrl+Alt+Space first)
  ./main.py --text     type instead of speaking
  ./main.py --mute     print replies instead of speaking (still synthesizes, for timing)
  ./main.py --text --mute   quickest way to try the brain

Ctrl+Alt+K stops speech and any action. Ctrl+C or "Kiku, quit" quits.
"""
import argparse
import os
import signal
import threading
import time
import traceback

import perf
from ui.overlay import Overlay


def log(msg):
    print(f"[KIKU] {msg}", flush=True)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def _force_quit(*_):
    os.write(2, b"[KIKU] forced quit\n")
    os._exit(130)


def _preload(name, fn):
    """Run fn() on a thread (models load in parallel). Returns get() -> result; re-raises its error."""
    box = {}

    def run():
        t = time.monotonic()
        try:
            box["v"] = fn()
        except BaseException as e:
            box["e"] = e
        perf.add(name, t)

    th = threading.Thread(target=run, name=f"kiku-load-{name}", daemon=True)
    th.start()

    def get():
        while th.is_alive():
            th.join(0.1)   # short joins so Ctrl+C still works while loading
        if "e" in box:
            raise box["e"]
        return box["v"]
    return get


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="type commands instead of using the mic")
    ap.add_argument("--mute", action="store_true", help="don't play audio")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGINT, signal.default_int_handler)   # even if started with SIGINT ignored
    perf.start_turn("startup")
    closers = []   # (name, fn), run in reverse on exit
    try:
        router, src, out, actions, ov, set_state = _start(args, closers)
        perf.end_turn()
        log("ready. Say \"Kiku, ...\"" + (" (or type)" if args.text else " or press the PTT hotkey")
            + " Say \"Kiku, quit\" or press Ctrl+C to quit.")
        _loop(router, src, out, actions, ov, set_state)
    except KeyboardInterrupt:
        log("interrupted (Ctrl+C or SIGTERM)")
    finally:
        signal.signal(signal.SIGINT, _force_quit)   # a second Ctrl+C doesn't wait for cleanup
        _shutdown(closers)
    return 0


def _start(args, closers):
    """Load everything; slow parts (LLM warm-up, TTS, STT + mic calibration) in parallel."""
    with perf.stage("overlay"):
        ov = Overlay()
        up = ov.available

        def close_overlay():   # hide() also clears highlights and the caption
            if up:
                ov.hide()
            ov.close()
        closers.append(("overlay", close_overlay))
        if up:
            ov.show()
            ov.set_state("idle")
            set_state = ov.set_state
        else:
            log("kiku-shell is not running; continuing without the overlay")
            set_state = lambda state: None

    from brain.llm import Llm
    from voice.sources import LocalMic, LocalSpeaker, TextConsole
    get_llm = _preload("llm load", Llm)
    get_out = _preload("tts load", lambda: LocalSpeaker(play=not args.mute))
    get_src = _preload("stt + mic", lambda: TextConsole(set_state) if args.text else LocalMic(set_state))

    with perf.stage("tools"):
        from brain.router import Router
        from browser import geometry
        from browser.bridge import Bridge
        from control.actions import Actions
        from control.locate import Locator
        from tools import gmail, open_app, open_website, page_reader
        actions = Actions(ov)
        locator = Locator(ov)
        open_app.init(ov, actions, locator)
        bridge = Bridge()
        # several Firefox windows: commands go to the tab in the one GNOME has focused
        bridge.window_title = lambda: (geometry.firefox_window(ov.list_windows()) or {}).get("title")
        bridge.start()   # the Firefox extension connects whenever Firefox runs
        closers.append(("bridge", bridge.stop))
        open_website.init(ov, actions, locator, bridge)

    llm = get_llm()
    closers.append(("llm", llm.close))
    out = get_out()
    closers.append(("speaker", out.close))
    src = get_src()
    closers.append(("mic", src.close))

    def stop_now():   # added last, runs first: stop speech and any running action (typing, moves)
        out.stop()
        if up:
            actions.abort()
    closers.append(("actions", stop_now))

    page_reader.init(ov, actions, bridge, llm)

    def speak(text):   # tools that talk while they work (gmail reads rows as it highlights them)
        set_state("talk")
        out.say(text)
        set_state("think")

    gmail.init(ov, actions, bridge, llm, say=speak)
    router = Router(llm)

    def on_hotkey(name):
        if name == "panic":
            log("panic: stopping speech")
            out.stop()
        elif name == "ptt":
            out.stop()   # talking over Kiku: cut it off and listen
            src.push_to_talk()

    ov.on_hotkey(on_hotkey)
    return router, src, out, actions, ov, set_state


def _loop(router, src, out, actions, ov, set_state):
    from tools.registry import speakable
    while True:
        command = src.listen()
        if command is None:
            return
        if not command:
            continue
        t = time.monotonic()
        perf.start_turn(command)
        set_state("think")
        try:
            reply, route = router.handle(command)
        except Exception as e:   # a bug, not a tool failure (registry.run catches those)
            log(f"turn failed: {e!r}")
            traceback.print_exc()
            reply, route = "Sorry, something went wrong.", {"tool": "error", "via": "error"}
        if route["tool"] == "stop":
            out.stop()
            actions.abort()
        if reply:
            if not getattr(reply, "spoken", False):   # registry.Spoken: the tool already said it
                set_state("talk")
                with perf.stage("speak"):
                    out.say(speakable(reply))
            ov.show_caption("")   # page answers are captioned while spoken
        log(f"turn: {route['via']} {route['tool']}, heard -> spoken end {(time.monotonic() - t) * 1000:.0f} ms")
        perf.end_turn()
        if route["tool"] == "quit":
            return
        set_state("idle")
        ov.go_home(wait=False)   # task done: back to the start spot


def _shutdown(closers):
    """Stop actions and speech, close mic, speaker, LLM keep-warm, bridge, then the overlay."""
    log("shutting down")
    for name, fn in reversed(closers):
        t = time.monotonic()
        try:
            fn()
        except Exception as e:
            log(f"shutdown: {name} failed: {e!r}")
        else:
            log(f"shutdown: {name} closed in {(time.monotonic() - t) * 1000:.0f} ms")
    log("bye")


if __name__ == "__main__":
    raise SystemExit(main())
