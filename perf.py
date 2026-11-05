"""Stage timings for one turn (heard -> spoken), printed as a table.

  perf.start_turn("open wikipedia")
  with perf.stage("llm"): ...          # or perf.add("stt", t_start, t_end)
  perf.end_turn()                      # prints the table (if config.PROFILE)

Stages added before start_turn() (e.g. STT, which runs before the command is known) are kept
and belong to the next turn. Thread-safe: the page reader's LLM thread adds stages too.
Repeated stages (several icon moves) are summed into one row with a count.
"""
import contextlib
import threading
import time

import config

_lock = threading.Lock()
_turn = {"label": "", "t0": None, "stages": []}


def _log(msg):
    print(f"[KIKU] {msg}", flush=True)


def start_turn(label):
    with _lock:
        early = _turn["stages"] if _turn["t0"] is None else []
        _turn.update(label=label, t0=time.monotonic(), stages=early)


def add(name, t_start, t_end=None):
    with _lock:
        _turn["stages"].append((name, t_start, time.monotonic() if t_end is None else t_end))


@contextlib.contextmanager
def stage(name):
    t = time.monotonic()
    try:
        yield
    finally:
        add(name, t)


def table(label, t0, t_end, stages):
    """Rows as text lines. Columns: start offset from t0, summed duration, share of the turn."""
    total = max(t_end - min([t0] + [s for _, s, _ in stages]), 1e-9)
    rows = {}
    for name, s, e in stages:
        r = rows.setdefault(name, [s, 0.0, 0])
        r[0], r[1], r[2] = min(r[0], s), r[1] + (e - s), r[2] + 1
    lines = [f"timing: {label!r}, total {total * 1000:.0f} ms",
             f"  {'stage':<26}{'start':>8}{'ms':>8}{'%':>6}  n"]
    for name, (s, d, n) in sorted(rows.items(), key=lambda kv: kv[1][0]):
        lines.append(f"  {name:<26}{(s - t0) * 1000:>8.0f}{d * 1000:>8.0f}{d / total * 100:>6.0f}  {n}")
    return lines


def end_turn():
    """Print the table for the current turn and reset."""
    with _lock:
        label, t0, stages = _turn["label"], _turn["t0"], list(_turn["stages"])
        _turn.update(label="", t0=None, stages=[])
    if t0 is None or not config.PROFILE:
        return
    for line in table(label, t0, time.monotonic(), stages):
        _log(line)
