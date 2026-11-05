"""Spoken errors (tools/registry.py) and the timing table (perf.py). No Ollama, shell or browser needed.

  venv/bin/python -m tests.test_errors
"""
import perf
from tools import registry


def test_sorry_says_failed_messages():
    from control.actions import Failed
    assert registry.sorry(Failed("I couldn't switch to Files"), "x") == "Sorry, I couldn't switch to Files."
    assert (registry.sorry(Failed("I couldn't find an app called fil. Did you mean Files?"), "x")
            == "Sorry, I couldn't find an app called fil. Did you mean Files?")


def test_sorry_hides_other_exceptions():
    from control.actions import Failed
    for e in [Failed(""), RuntimeError("Event loop is closed"),
              RuntimeError("Overlay methods can't be called from the D-Bus thread"),
              ValueError("invalid literal for int() with base 10: 'x'"),
              OSError(2, "No such file or directory: '/tmp/x.png'"),
              RuntimeError("get_url timed out after 2.0s"),
              RuntimeError("")]:
        got = registry.sorry(e, "I couldn't open that website")
        assert got == "Sorry, I couldn't open that website.", (e, got)


def test_speakable():
    ok = "The paragraph explains why error correction matters (in short, noise)."
    assert registry.speakable(ok) == ok
    for crash in ['Traceback (most recent call last):\n  File "x.py", line 3', "KeyError: 'url'",
                  "<dbus_next.message.Message object at 0x7f00>"]:
        assert registry.speakable(crash) == "Sorry, something went wrong.", crash


def test_run_never_raises():
    from control.actions import Aborted

    def boom(ctx):
        raise KeyError("url")

    def stopped(ctx):
        raise Aborted()

    registry.register("_boom", "test")(boom)
    registry.register("_stopped", "test")(stopped)
    try:
        assert registry.run("_boom", {}, None) == "Sorry, something went wrong while doing that."
        assert registry.run("_stopped", {}, None) == "Stopped."
    finally:
        registry.TOOLS.pop("_boom")
        registry.TOOLS.pop("_stopped")


def test_perf_table():
    t0 = 100.0
    lines = perf.table("open x", t0, t0 + 2.0, [("stt", 99.5, 99.9), ("move", 100.1, 100.3),
                                                 ("move", 100.5, 100.7), ("llm", 100.2, 101.2)])
    assert "total 2500 ms" in lines[0], lines[0]   # starts at the earliest stage (STT before the turn)
    rows = {ln.split()[0]: ln.split() for ln in lines[2:]}
    assert [ln.split()[0] for ln in lines[2:]] == ["stt", "move", "llm"]   # by start time
    assert rows["move"][1:] == ["100", "400", "16", "2"], rows["move"]     # summed, count 2
    assert rows["stt"][1] == "-500"


def test_perf_turn_keeps_early_stages():
    perf.end_turn()
    perf.add("stt", 1.0, 1.5)
    perf.start_turn("cmd")
    assert [s[0] for s in perf._turn["stages"]] == ["stt"]
    perf.start_turn("next")                 # a new turn without end_turn: old stages dropped
    assert perf._turn["stages"] == []
    perf.end_turn()


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"[KIKU] test: ok  {name}")
            except AssertionError as e:
                fails += 1
                print(f"[KIKU] test: BAD {name}: {e}")
    print(f"[KIKU] test: errors {'all passed' if not fails else f'{fails} failed'}")
    sys.exit(fails != 0)
