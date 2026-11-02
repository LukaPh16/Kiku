"""Find things on screen by name and return their global (stage) rect.

Order: 1) kiku-shell data (windows, dock icons), 2) AT-SPI inside a window (element extents
relative to the window + the window's frame from ListWindows), 3) OCR on a Screenshot, 4) None.
Never guesses: weak matches are rejected and every miss is logged with what was tried.
"""
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import config


def _log(msg):
    print(f"[KIKU] locate: {msg}", flush=True)


@dataclass
class Target:
    name: str
    x: float
    y: float
    w: float
    h: float
    source: str                 # window | dock | atspi | ocr
    score: float = 1.0
    info: dict = field(default_factory=dict)

    @property
    def center(self):
        return self.x + self.w / 2, self.y + self.h / 2

    @property
    def rect(self):
        return self.x, self.y, self.w, self.h

    def __str__(self):
        return (f"{self.source} {self.name!r} at ({self.x:.0f},{self.y:.0f} {self.w:.0f}x{self.h:.0f}) "
                f"score {self.score:.2f}")


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


def text_score(query, text):
    """1.0 exact (case/punct-insensitive), 0.9 all query words in text, else fuzzy ratio."""
    q, t = " ".join(_norm(query)), " ".join(_norm(text))
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if all(w in t.split() for w in q.split()):
        return 0.9 - 0.1 * min(1.0, (len(t) - len(q)) / 40)   # prefer short labels
    return SequenceMatcher(None, q, t).ratio() * 0.85


def _rect(r):
    return r["x"], r["y"], r["width"], r["height"]


class Locator:
    def __init__(self, shell):
        self.shell = shell
        self._ocr = None

    # ---- 1) kiku-shell data ----

    def window(self, name=None, app_id=None):
        """Best visible window by app id or by title/app name."""
        best = None
        for w in self.shell.list_windows():
            if w["minimized"] or not w["on_active_workspace"]:
                continue
            if app_id:
                score = 1.0 if w["app_id"] == app_id else 0.0
            else:
                score = max(text_score(name, w["title"]), text_score(name, w["app_id"].removesuffix(".desktop")))
            if score >= config.APP_MATCH_MIN and (best is None or score > best[0]):
                best = (score, w)   # list is top-of-stack first, so ties keep the topmost
        if not best:
            return None
        score, w = best
        return Target(w["title"], *_rect(w["frame"]), "window", score, {"window": w})

    def dock_icon(self, name=None, app_id=None):
        best = None
        for d in self.shell.dash_icons():
            if not d["visible"]:
                continue
            score = (1.0 if d["app_id"] == app_id else 0.0) if app_id else text_score(name, d["name"])
            if score >= config.APP_MATCH_MIN and (best is None or score > best[0]):
                best = (score, d)
        if not best:
            return None
        score, d = best
        return Target(d["name"], *_rect(d["rect"]), "dock", score, {"icon": d})

    # ---- 2) AT-SPI ----

    def atspi(self, name, window=None):
        """Control named `name` inside `window` (ListWindows dict; default: focused window)."""
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
        except (ImportError, ValueError) as e:
            _log(f"AT-SPI unavailable ({e})")
            return None
        window = window or next((w for w in self.shell.list_windows() if w["focused"]), None)
        if window is None:
            _log("AT-SPI: no focused window")
            return None
        frame = self._atspi_frame(Atspi, window)
        if frame is None:
            _log(f"AT-SPI: no accessible for window {window['title']!r} (pid {window['pid']}); "
                 f"app may not expose accessibility")
            return None
        fx, fy, fw, fh = _extents(Atspi, frame)
        best, seen, t0 = None, 0, time.monotonic()
        stack = [frame]
        while stack and seen < config.ATSPI_MAX_NODES and time.monotonic() - t0 < config.ATSPI_TIMEOUT_S:
            node = stack.pop()
            seen += 1
            try:
                states = node.get_state_set()
                if not states.contains(Atspi.StateType.SHOWING):
                    continue
                label = node.get_name() or ""
                if label and node is not frame:
                    score = text_score(name, label)
                    if score >= config.APP_MATCH_MIN:
                        x, y, w, h = _extents(Atspi, node)
                        # Rank: text score, then role kind (control > label > image > container),
                        # then the smallest (a "Home" view panel shouldn't beat the "Home" button).
                        rank = (score, _role_kind(Atspi, node), -(w * h))
                        if w > 0 and h > 0 and (best is None or rank > best[0]):
                            best = (rank, node, label, x, y, w, h)
                stack.extend(node.get_child_at_index(i) for i in range(node.get_child_count() - 1, -1, -1))
            except Exception:
                continue   # nodes vanish while we walk; skip them
        if not best:
            _log(f"AT-SPI: no {name!r} in {window['title']!r} ({seen} nodes)")
            return None
        (score, _, _), node, label, x, y, w, h = best
        # Element coords are relative to the toplevel surface; the frame accessible tells us where
        # the window frame sits in that space (0,0 for most toolkits, the shadow offset for some).
        ox, oy = window["frame"]["x"] - fx, window["frame"]["y"] - fy
        return Target(label, ox + x, oy + y, w, h, "atspi", score,
                      {"role": node.get_role_name(), "window": window})

    def _atspi_frame(self, Atspi, window):
        desktop = Atspi.get_desktop(0)
        candidates = []
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            try:
                if app is None or app.get_process_id() != window["pid"]:
                    continue
                for j in range(app.get_child_count()):
                    top = app.get_child_at_index(j)
                    if top is not None:
                        candidates.append(top)
            except Exception:
                continue
        if not candidates:
            return None
        # Prefer the toplevel whose name is the window title, then the active one.
        def rank(top):
            try:
                active = top.get_state_set().contains(Atspi.StateType.ACTIVE)
                return (text_score(window["title"], top.get_name() or ""), active)
            except Exception:
                return (0, False)
        return max(candidates, key=rank)

    # ---- 3) OCR ----

    def ocr(self, name, region=None):
        """Text `name` on screen via OCR; region = (x, y, w, h) in stage px to search in."""
        path = self.shell.screenshot()
        if not path:
            _log("OCR: screenshot failed")
            return None
        import cv2
        img = cv2.imread(path)
        if img is None:
            _log(f"OCR: can't read {path}")
            return None
        # Image px -> stage px. Plain ratio of sizes, so scaling doesn't matter here.
        mons = self.shell.get_monitors()
        sx0 = min(m["x"] for m in mons)
        sy0 = min(m["y"] for m in mons)
        stage_w = max(m["x"] + m["width"] for m in mons) - sx0
        k = img.shape[1] / stage_w
        ox = oy = 0
        if region:
            rx, ry, rw, rh = region
            ox, oy = int((rx - sx0) * k), int((ry - sy0) * k)
            img = img[max(0, oy):int(oy + rh * k), max(0, ox):int(ox + rw * k)]
            ox, oy = max(0, ox), max(0, oy)
        t = time.monotonic()
        result = self._engine()(img)
        _log(f"OCR: {len(result.txts or ())} texts in {(time.monotonic() - t) * 1000:.0f} ms")
        best = None
        for text, score, box in zip(result.txts or (), result.scores or (), result.boxes if result.boxes is not None else ()):
            if score < config.OCR_MIN_SCORE:
                continue
            s = text_score(name, text)
            if s >= config.APP_MATCH_MIN and (best is None or s > best[0]):
                best = (s, text, box)
        try:
            import os
            os.remove(path)
        except OSError:
            pass
        if not best:
            _log(f"OCR: no {name!r} on screen")
            return None
        s, text, box = best
        xs, ys = [p[0] + ox for p in box], [p[1] + oy for p in box]
        x0, y0 = min(xs) / k + sx0, min(ys) / k + sy0
        return Target(text, x0, y0, (max(xs) - min(xs)) / k, (max(ys) - min(ys)) / k, "ocr", s)

    def _engine(self):
        if self._ocr is None:
            from rapidocr import RapidOCR
            self._ocr = RapidOCR(params={"Global.log_level": "error"})
        return self._ocr

    # ---- all together ----

    def find(self, name, window=None, sources=("window", "dock", "atspi", "ocr")):
        """First match in source order, or None (logged)."""
        t = time.monotonic()
        steps = {
            "window": lambda: self.window(name),
            "dock": lambda: self.dock_icon(name),
            "atspi": lambda: self.atspi(name, window),
            "ocr": lambda: self.ocr(name, _rect(window["frame"]) if window else None),
        }
        for src in sources:
            target = steps[src]()
            if target:
                _log(f"found {target} in {(time.monotonic() - t) * 1000:.0f} ms")
                return target
        _log(f"NOT FOUND: {name!r} (tried {', '.join(sources)}) in {(time.monotonic() - t) * 1000:.0f} ms")
        return None


def _extents(Atspi, node):
    e = node.get_extents(Atspi.CoordType.WINDOW)
    return e.x, e.y, e.width, e.height


def _role_kind(Atspi, node):
    """3 clickable control, 2 text label, 1 image/other, 0 container."""
    R = Atspi.Role
    role = node.get_role()
    if role in (R.PUSH_BUTTON, R.TOGGLE_BUTTON, R.MENU_ITEM, R.LINK, R.PAGE_TAB, R.CHECK_BOX,
                R.RADIO_BUTTON, R.ENTRY, R.LIST_ITEM, R.COMBO_BOX, R.TABLE_CELL, R.TREE_ITEM):
        return 3
    if role in (R.LABEL, R.STATIC, R.HEADING, R.TEXT, R.CAPTION):
        return 2
    if role in (R.PANEL, R.FILLER, R.SCROLL_PANE, R.VIEWPORT, R.FRAME, R.WINDOW, R.SECTION,
                R.GROUPING, R.LIST, R.LIST_BOX, R.TREE, R.TABLE, R.TREE_TABLE, R.PAGE_TAB_LIST,
                R.TOOL_BAR, R.MENU_BAR, R.SPLIT_PANE, R.DOCUMENT_FRAME, R.APPLICATION):
        return 0
    return 1
