"""go_back: "go back" in the window you're using. tools/gmail.py registers the "back" tool: an open
email goes back to the list there; everything else comes here.

Firefox     -> icon to the back button, Alt+Left. The bridge (else the window title) tells whether
               the page really changed.
Other apps  -> icon to the title bar, Alt+Left (Files, Settings, Help and most GNOME apps). The
               window title tells whether it worked.
"""
import time

import config
from browser import geometry
from browser.bridge import BridgeError
from control.actions import Aborted, Failed
from tools import close, open_app, open_website
from tools.registry import sorry

_shell = _actions = _bridge = None


def _log(msg):
    print(f"[KIKU] back: {msg}", flush=True)


def init(shell, actions, bridge):
    global _shell, _actions, _bridge
    _shell, _actions, _bridge = shell, actions, bridge


def go_back(ctx):
    if _shell is None:
        return "Desktop control isn't set up."
    _actions.begin()
    t = time.monotonic()
    try:
        win = close.front_window(_shell.list_windows())
        if win is None:
            raise Failed("there's no window to go back in")
        _focus(win)
        reply = _firefox(win, ctx) if geometry.is_firefox(win) else _app(win)
    except Aborted:
        _log("aborted")
        return "Stopped."
    except RuntimeError as e:
        _log(f"failed: {e}")
        return sorry(e, "I couldn't go back")
    finally:
        _shell.clear_highlight()
    _log(f"{reply} ({(time.monotonic() - t) * 1000:.0f} ms)")
    return reply


def _firefox(win, ctx):
    before = _tab()
    title = close.page_title(win)
    f = win["frame"]
    bar_y = open_website._urlbar(win).center[1]
    _actions.key("alt+Left", target=(f["x"] + 24, bar_y))   # the back button: left end of the nav bar
    if before:
        def moved():
            t = _tab()
            return t and (t["url"] != before["url"] or t["load_seq"] > before["load_seq"])
        if not _wait(moved):
            return "There's no page to go back to."
        ctx.focus["page"] = (_tab() or {}).get("url")
    elif not _wait(lambda: _title(win) not in (None, win["title"])):
        return "Nothing changed. There may be no page to go back to."
    new = close.page_title(_window(win["id"]) or win)
    return f"Back to {new}." if new and new != title else "Went back."


def _app(win):
    name = next((a["name"] for a in _shell.list_apps() if a["app_id"] == win["app_id"]), "that app")
    title = win["title"]
    _actions.key("alt+Left", target=open_app._title_bar(win))
    if not _wait(lambda: _title(win) not in (None, title), 1.5):
        return f"Nothing changed in {name}. There may be nothing to go back to."
    new = _title(win)
    return f"Back to {new}." if new else "Went back."


def _tab():
    if _bridge is None or not _bridge.connected:
        return None
    try:
        return _bridge.call("get_url", timeout=2)
    except BridgeError as e:
        _log(f"get_url: {e}")
        return None


def _window(win_id):
    return next((w for w in _shell.list_windows() if w["id"] == win_id), None)


def _title(win):
    w = _window(win["id"])
    return w["title"] if w else None


def _focus(w):
    if w["focused"]:
        return
    _shell.activate_window(w["id"])
    if not _wait(lambda: (_window(w["id"]) or {}).get("focused"), 1.0):
        raise Failed("that window didn't take focus")


def _wait(done, seconds=None):
    end = time.monotonic() + (config.BACK_WAIT_S if seconds is None else seconds)
    while True:
        if done():
            return True
        if time.monotonic() > end:
            return False
        _actions.wait(0.1)
