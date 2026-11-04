"""open_website: open a site (or search) in Firefox, visibly.

Firefox focused (open_app opens or switches to it) -> icon moves to the address bar (AT-SPI,
else an estimate) -> Ctrl+L -> type the site -> Enter -> wait for the page to load via the bridge.
Known spoken names map to URLs (config.KNOWN_SITES); "reddit.com" is typed as is; anything else
is typed as a search, which Firefox sends to its default search engine.
"""
import re
import time

import config
from browser import geometry
from browser.bridge import BridgeError
from control.actions import Aborted
from control.locate import Target
from tools import open_app
from tools.registry import register

_shell = _actions = _locator = _bridge = None
_urlbar_cache = {}   # (window id, w, h) -> address bar rect relative to the window frame

_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(:\d+)?(/\S*)?$")


def _log(msg):
    print(f"[KIKU] open_website: {msg}", flush=True)


def init(shell, actions, locator, bridge):
    global _shell, _actions, _locator, _bridge
    _shell, _actions, _locator, _bridge = shell, actions, locator, bridge


# ---- what to type ----

def resolve_site(site):
    """Spoken site -> (kind, text to type). kind is "url" or "search"."""
    s = " ".join(site.lower().split()).rstrip(".")
    s = re.sub(r"\s+dot\s+", ".", s)
    if s in config.KNOWN_SITES:
        return "url", _short(config.KNOWN_SITES[s])
    if "://" in s:
        return "url", s
    if _DOMAIN.match(s.replace(" ", "")) and " " not in s.strip():
        return "url", s
    return "search", " ".join(site.split()).rstrip(".")


def _short(url):
    """What we type: no scheme, no www, no trailing slash (Firefox fills those in)."""
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.rstrip("/")


# ---- the tool ----

@register("open_website", "Open a website in Firefox (name or domain), or search the web for it",
          {"site": {"type": "string"}})
def open_website(ctx, site):
    if _shell is None:
        return "Desktop control isn't set up."
    kind, text = resolve_site(site)
    _log(f"{site!r} -> {kind} {text!r}")
    _actions.begin()
    t = time.monotonic()
    try:
        info = _open(text)
    except Aborted:
        _log("aborted")
        return "Stopped."
    except RuntimeError as e:
        _log(f"failed: {e}")
        return f"Sorry, {e}."
    finally:
        _shell.clear_highlight()
    ctx.focus["app"] = "Firefox"
    ctx.focus["page"] = info["url"] if info else (text if kind == "url" else None)
    _log(f"done in {(time.monotonic() - t) * 1000:.0f} ms: {info and info['url']}")
    if info is None:
        return f"Opening {site}." if kind == "url" else f"Searching for {text}."
    if info.get("timed_out"):
        return f"{site} is still loading."
    return f"Opened {site}." if kind == "url" else f"Here are the results for {text}."


def _open(text):
    win = focus_firefox()
    urlbar = _urlbar(win)
    before = _tab_info()
    if urlbar.source == "atspi":
        _shell.highlight_rect(*urlbar.rect)
    _actions.key("ctrl+l", target=urlbar)
    _actions.type_text(text, target=urlbar)
    _actions.key("enter", target=urlbar)
    _shell.clear_highlight()
    return _wait_load(before)


def focus_firefox():
    """Focused Firefox window dict; opens/switches to Firefox if needed (icon goes there first).
    Also used by tools/page_reader.py. Caller calls actions.begin(). Raises RuntimeError / Aborted."""
    win = next((w for w in _shell.list_windows() if w["focused"]), None)
    if win and geometry.is_firefox(win):
        return win
    _, launched = open_app.ensure_open(config.FIREFOX_APP)
    end = time.monotonic() + 1.5
    while True:
        win = next((w for w in _shell.list_windows() if w["focused"]), None)
        if win and geometry.is_firefox(win):
            break
        if time.monotonic() > end:
            raise RuntimeError("Firefox didn't take focus")
        _actions.wait(0.05)
    if launched and _bridge is not None and not _bridge.connected:
        # A fresh Firefox: its extension connecting means the window is ready for input.
        _log("waiting for the Firefox extension to connect")
        end = time.monotonic() + config.BROWSER_CONNECT_WAIT_S
        while not _bridge.connected and time.monotonic() < end:
            _actions.wait(0.1)
        if not _bridge.connected:
            _log("WARNING: extension didn't connect (installed? token set?); continuing without it")
    return win


def _urlbar(win):
    """Address bar Target: cached, else AT-SPI, else an estimate from the window frame."""
    f = win["frame"]
    key = (win["id"], f["width"], f["height"])
    if key in _urlbar_cache:
        dx, dy, w, h = _urlbar_cache[key]
        return Target("address bar", f["x"] + dx, f["y"] + dy, w, h, "atspi")
    t0 = time.monotonic()
    t = _locator.atspi(config.URLBAR_NAME, win) if _locator else None
    if t and 0 <= t.y - f["y"] < 160:   # must be in the toolbar, not a search box in the page
        _log(f"address bar via AT-SPI in {(time.monotonic() - t0) * 1000:.0f} ms: {t}")
        _urlbar_cache[key] = (t.x - f["x"], t.y - f["y"], t.w, t.h)
        return t
    # Default Firefox layout: tab strip (~44 px) then the nav bar; the address bar fills the middle.
    est = Target("address bar (estimated)", f["x"] + f["width"] * 0.15, f["y"] + 48,
                 f["width"] * 0.55, 32, "estimate")
    _log(f"address bar not found via AT-SPI; using Ctrl+L and estimate {est}")
    return est


def _tab_info():
    if _bridge is None or not _bridge.connected:
        return None
    try:
        return _bridge.call("get_url", timeout=2)
    except BridgeError as e:
        _log(f"get_url failed: {e}")
        return None


def _wait_load(before):
    """Wait until a new load in the active tab completes. None if the bridge never answered."""
    if _bridge is None:
        return None
    end = time.monotonic() + config.WEB_LOAD_TIMEOUT_S
    last = None
    while time.monotonic() < end:
        info = _tab_info()
        if info:
            last = info
            started = (before is None or info["tab_id"] != before["tab_id"]
                       or info["load_seq"] > before["load_seq"])
            if started and info["status"] == "complete" and not info["url"].startswith("about:"):
                return info
        _actions.wait(0.15)
    if last is None:
        _log("no answer from the extension; can't tell when the page loaded")
        return None
    _log(f"page still loading after {config.WEB_LOAD_TIMEOUT_S:.0f}s ({last['url']})")
    return dict(last, timed_out=True)
