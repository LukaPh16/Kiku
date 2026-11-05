"""close: close the focused window, the current Firefox tab, an app, or a tab by its name. Visibly.

"close" / "close this window"  -> the focused window
"close this tab"               -> Ctrl+W in Firefox
"close firefox"                -> every window of that (running) app
"close the wikipedia tab"      -> the Firefox tab whose title or site matches (all tabs through the
                                  bridge; without it, only the tab showing in each Firefox window)
The icon goes to the window's title bar first. Windows close through kiku-shell's CloseWindow (like
the close button), or Alt+F4 on an older kiku-shell. Kiku then checks it really closed, and says so
if the app asked something first (unsaved changes, "close 5 tabs?").
"""
import os
import time

import config
from browser import geometry
from browser.bridge import BridgeError
from control.actions import Aborted, Failed
from control.locate import text_score
from tools import open_app
from tools.registry import register, sorry

_shell = _actions = _bridge = None
_FF_SUFFIXES = (" — Mozilla Firefox", " - Mozilla Firefox", " — Firefox", " - Firefox")


def _log(msg):
    print(f"[KIKU] close: {msg}", flush=True)


def init(shell, actions, bridge):
    global _shell, _actions, _bridge
    _shell, _actions, _bridge = shell, actions, bridge


@register("close", "Close the current window, the current tab, an app, or a browser tab by its name",
          {"target": {"type": "string", "required": False}})
def close(ctx, target=None):
    if _shell is None:
        return "Desktop control isn't set up."
    target = " ".join((target or "").lower().split())
    _log(f"target {target or 'window'!r}")
    _actions.begin()
    t = time.monotonic()
    try:
        reply = _close(target)
    except Aborted:
        _log("aborted")
        return "Stopped."
    except RuntimeError as e:
        _log(f"failed: {e}")
        return sorry(e, "I couldn't close that")
    finally:
        _shell.clear_highlight()
    _log(f"{reply} ({(time.monotonic() - t) * 1000:.0f} ms)")
    return reply


def _ancestors():
    """Our process and its parents: the terminal running Kiku is one of them."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def front_window(windows):
    """The window the user means by "this": the focused one, unless that is the terminal running Kiku
    (typing in --text mode focuses it); then the topmost visible other window. None if there is none."""
    own = _ancestors()
    visible = [w for w in windows if not w["minimized"] and w["on_active_workspace"] and w.get("pid") not in own]
    return next((w for w in visible if w["focused"]), visible[0] if visible else None)


def _close(target):
    if target in ("", "window", "it", "this", "that"):
        w = front_window(_shell.list_windows())
        if w is None:
            raise Failed("there's no window to close")
        return _close_windows([w], _app_name(w))
    if target in ("tab", "the tab", "this tab", "current tab"):
        return _close_current_tab()
    name = target.removesuffix(" tab").strip()
    if name == target:   # "close firefox": a running app first, else a tab with that name
        app = _running_app(name)
        if app:
            return _close_windows([w for w in _shell.list_windows() if w["app_id"] == app["app_id"]],
                                  app["name"])
    return _close_named_tab(name)


# ---- windows ----

def _app_name(w):
    app = next((a for a in _shell.list_apps() if a["app_id"] == w["app_id"]), None)
    return app["name"] if app else "the window"


def _running_app(name):
    """App dict for a spoken name among apps with open windows, else None. Raises Failed if ambiguous."""
    ids = {w["app_id"] for w in _shell.list_windows()}
    apps = [a for a in _shell.list_apps() if a["app_id"] in ids]
    match = open_app.resolve(name, apps)
    if isinstance(match, dict):
        return match
    if match.startswith("Which one") or match.startswith("There are"):
        raise Failed(match.removesuffix("?").removesuffix("."))
    return None


def _close_windows(wins, name):
    for w in wins:
        if not w["minimized"] and w["on_active_workspace"]:
            _actions.point_at(open_app._title_bar(w))
        ok = _shell.close_window(w["id"])
        if ok is None:   # kiku-shell from before CloseWindow (log out and in to update): Alt+F4
            _focus(w)
            _actions.key("alt+F4", target=open_app._title_bar(w))
        elif not ok:
            raise Failed(f"{name} can't be closed that way")
    ids = {w["id"] for w in wins}
    if _wait(lambda: not ids & {w["id"] for w in _shell.list_windows()}):
        return f"Closed {name}."
    return f"{name} is asking something before it closes."


def _focus(w):
    if w["focused"]:
        return
    if not _shell.activate_window(w["id"]):
        raise Failed("I couldn't switch to that window")
    if not _wait(lambda: any(x["id"] == w["id"] and x["focused"] for x in _shell.list_windows()), 1.0):
        raise Failed("that window didn't take focus")


def _wait(done, seconds=None):
    end = time.monotonic() + (config.CLOSE_WAIT_S if seconds is None else seconds)
    while True:
        if done():
            return True
        if time.monotonic() > end:
            return False
        _actions.wait(0.1)


# ---- Firefox tabs ----

def page_title(w):
    title = w.get("title") or ""
    for s in _FF_SUFFIXES:
        if title.endswith(s):
            return title[:-len(s)]
    return "" if title in ("Mozilla Firefox", "Firefox") else title


def _window(win_id):
    return next((w for w in _shell.list_windows() if w["id"] == win_id), None)


def _active_tab_id():
    if _bridge is None or not _bridge.connected:
        return None
    try:
        return (_bridge.call("get_url") or {}).get("tab_id")
    except BridgeError as e:
        _log(f"get_url: {e}")
        return None


def _ctrl_w(win, tab_id=None):
    """Ctrl+W in a Firefox window; returns True once that tab is gone (tab id, else the title changed)."""
    _focus(win)
    before_title = page_title(win)
    before_tab = tab_id if tab_id is not None else _active_tab_id()
    _actions.key("ctrl+w", target=open_app._title_bar(win))   # tabs sit in the title bar

    def closed():
        w = _window(win["id"])
        if w is None:
            return True   # it was the last tab, so the window went too
        if before_tab is not None and _bridge.connected:
            try:
                return before_tab not in {t["tab_id"] for t in _bridge.call("find_tabs", host="*")}
            except BridgeError:
                pass
        return page_title(w) != before_title
    return _wait(closed)


def _close_current_tab():
    win = geometry.firefox_window(_shell.list_windows())
    if win is None:
        raise Failed("Firefox isn't open")
    title = page_title(win)
    if _ctrl_w(win):
        return "Closed the tab." if not title else f"Closed {title}."
    return "The tab didn't close. Firefox may be asking something."


def _tab_score(name, title, url=""):
    host = url.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return max(text_score(name, title), text_score(name, host),
               text_score(name, host.rsplit(".", 1)[0]))   # "wikipedia" ~ en.wikipedia.org


def _close_named_tab(name):
    ff = [w for w in _shell.list_windows() if geometry.is_firefox(w)]
    if not ff:
        raise Failed(f"I couldn't find a window or tab called {name}")
    if _bridge is not None and _bridge.connected:
        try:
            tabs = _bridge.call("find_tabs", host="*")   # every http(s) tab, in all windows
        except BridgeError as e:
            _log(f"find_tabs: {e}")
            tabs = []
        ranked = sorted(((_tab_score(name, t["title"], t["url"]), t) for t in tabs), key=lambda p: -p[0])
        if ranked and ranked[0][0] >= config.TAB_MATCH_MIN:
            tab = ranked[0][1]
            _log(f"{name!r} -> tab {tab['title']!r} ({tab['url']}), score {ranked[0][0]:.2f}")
            _bridge.call("activate_tab", tab_id=tab["tab_id"])
            # its window now shows the tab's title; find that window in the shell
            if not _wait(lambda: _tab_window(tab["title"]) is not None, 1.0):
                raise Failed("I found the tab, but not its window")
            if _ctrl_w(_tab_window(tab["title"]), tab["tab_id"]):
                return f"Closed {tab['title'] or name}."
            return "The tab didn't close. Firefox may be asking something."
    # without the bridge (or no match there): the tab showing in each Firefox window
    ranked = sorted(((_tab_score(name, page_title(w)), w) for w in ff), key=lambda p: -p[0])
    if ranked[0][0] >= config.TAB_MATCH_MIN:
        win = ranked[0][1]
        title = page_title(win)
        _log(f"{name!r} -> window showing {title!r}, score {ranked[0][0]:.2f}")
        if _ctrl_w(win):
            return f"Closed {title}."
        return "The tab didn't close. Firefox may be asking something."
    if _bridge is None or not _bridge.connected:
        _log("bridge not connected: only the tab showing in each Firefox window can be found")
    raise Failed(f"I couldn't find a window or tab called {name}")


def _tab_window(title):
    return next((w for w in _shell.list_windows() if geometry.is_firefox(w)
                 and title and page_title(w) == title), None)
