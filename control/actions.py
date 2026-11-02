"""Visible desktop actions. Every action first moves Kiku's icon to the target and hovers,
then acts. A panic stop (hotkey, "kiku stop", abort()) raises Aborted at the next step.
"""
import threading
import time

import config


class Aborted(Exception):
    pass


def _log(msg):
    print(f"[KIKU] action: {msg}", flush=True)


class Actions:
    def __init__(self, shell):
        self.shell = shell
        self._aborted = threading.Event()
        shell.on_hotkey(lambda name: name == "panic" and self._aborted.set())

    # ---- abort ----

    def begin(self):
        """Call at the start of each command: clears an earlier abort."""
        self._aborted.clear()

    def abort(self):
        """Stop now (e.g. "kiku stop"): the shell drops pending motion/input and releases keys."""
        self._aborted.set()
        self.shell.abort()

    @property
    def aborted(self):
        return self._aborted.is_set()

    def check(self):
        if self._aborted.is_set():
            raise Aborted()

    # ---- helpers ----

    def point_at(self, target, hover_ms=None):
        """Move the icon next to target (Target or (x, y)) and rest briefly."""
        self.check()
        x, y = target.center if hasattr(target, "center") else target
        self.shell.hover(x, y, config.HOVER_MS if hover_ms is None else hover_ms)
        self.check()
        return x, y

    def _focus_point(self):
        w = next((w for w in self.shell.list_windows() if w["focused"]), None)
        if w:
            f = w["frame"]
            return f["x"] + f["width"] / 2, f["y"] + min(60, f["height"] / 2)
        p = self.shell.get_pointer()
        return p if p else (0, 0)

    # ---- actions ----

    def click(self, target, button=1):
        t = time.monotonic()
        x, y = self.point_at(target)
        self.shell.pointer_move(x, y)
        self.shell.click_pulse()
        ok = self.shell.click(button)
        self.check()
        if not ok:
            raise RuntimeError("click failed")
        _log(f"click {button} at ({x:.0f},{y:.0f}) in {(time.monotonic() - t) * 1000:.0f} ms")

    def type_text(self, text, target=None, cps=None):
        """Type into whatever has keyboard focus. The icon points at target (or the focused window)."""
        t = time.monotonic()
        self.point_at(target or self._focus_point())
        skipped = self.shell.type_text(text, cps or config.TYPE_CPS)
        if skipped == -1 or self.aborted:
            raise Aborted()
        if skipped is None:
            raise RuntimeError("typing failed")
        if skipped:
            _log(f"WARNING: {skipped} characters have no key on this layout")
        _log(f"typed {len(text)} chars in {(time.monotonic() - t) * 1000:.0f} ms")

    def key(self, combo, target=None):
        self.point_at(target or self._focus_point())
        ok = self.shell.key_combo(combo)
        self.check()
        if not ok:
            raise RuntimeError(f"key {combo!r} failed")
        _log(f"key {combo}")

    def scroll(self, dy, dx=0, target=None):
        """Scroll the window under target (+dy = down, in wheel steps). Moves the real pointer there."""
        x, y = self.point_at(target or self._focus_point())
        self.shell.pointer_move(x, y)
        ok = self.shell.scroll(dx, dy)
        self.check()
        if not ok:
            raise RuntimeError("scroll failed")
        _log(f"scroll dx={dx} dy={dy} at ({x:.0f},{y:.0f})")

    def wait(self, seconds):
        """Abortable sleep."""
        if self._aborted.wait(seconds):
            raise Aborted()
