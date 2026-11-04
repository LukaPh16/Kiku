"""Browser layer tests without Firefox: site names, page -> screen geometry, and the bridge
against a fake extension (a websockets client).

  venv/bin/python -m tests.test_browser
"""
import asyncio
import json
import threading
import time

from websockets.asyncio.client import connect

from browser import geometry
from browser.bridge import Bridge, BridgeError
from brain.context import Context
from tools import open_website
from tools.open_website import resolve_site

TOKEN = "test-token"
PORT = 18766


def log(msg):
    print(f"[KIKU] test: {msg}", flush=True)


# ---- open_website: what gets typed ----

SITES = {
    "wikipedia": ("url", "en.wikipedia.org"),
    "reddit": ("url", "reddit.com"),
    "YouTube": ("url", "youtube.com"),
    "stack overflow": ("url", "stackoverflow.com"),
    "reddit.com": ("url", "reddit.com"),
    "youtube dot com": ("url", "youtube.com"),
    "wikipedia.org.": ("url", "wikipedia.org"),
    "news.ycombinator.com/newest": ("url", "news.ycombinator.com/newest"),
    "https://example.org/a": ("url", "https://example.org/a"),
    "bbc": ("search", "bbc"),
    "weather": ("search", "weather"),
    "best pizza in tbilisi": ("search", "best pizza in tbilisi"),
}


def test_resolve_site():
    bad = []
    for spoken, want in SITES.items():
        got = resolve_site(spoken)
        log(f"{'ok ' if got == want else 'BAD'} {spoken!r} -> {got}")
        if got != want:
            bad.append((spoken, got, want))
    assert not bad, bad


class _FakeShell:
    def __init__(self, windows):
        self.windows, self.log = windows, []

    def list_windows(self):
        return self.windows

    def highlight_rect(self, *r):
        self.log.append(("highlight", r))

    def clear_highlight(self):
        self.log.append(("clear",))


class _FakeActions:
    def __init__(self, on_enter=None):
        self.log, self.on_enter = [], on_enter

    def begin(self):
        pass

    def key(self, combo, target=None):
        self.log.append(("key", combo, target.center))
        if combo == "enter" and self.on_enter:
            self.on_enter()

    def type_text(self, text, target=None):
        self.log.append(("type", text, target.center))

    def wait(self, s):
        pass


class _FakeBridge:
    """get_url: the old page until Enter, then loading, then the new page complete."""
    connected = True

    def __init__(self):
        self.state = {"tab_id": 1, "url": "https://old.example/", "status": "complete", "load_seq": 4}
        self.polls = 0

    def enter(self):
        self.state = dict(self.state, url="https://en.wikipedia.org/", status="loading", load_seq=5)

    def call(self, cmd, timeout=None, **args):
        assert cmd == "get_url"
        if self.state["status"] == "loading":
            self.polls += 1
            if self.polls >= 3:
                self.state = dict(self.state, status="complete")
        return dict(self.state)


def test_open_website_flow():
    win = _win(0, 0, 1366, 768)
    shell, bridge = _FakeShell([win]), _FakeBridge()
    actions = _FakeActions(on_enter=bridge.enter)
    open_website.init(shell, actions, None, bridge)
    ctx = Context()
    reply = open_website.open_website(ctx, "wikipedia")
    log(f"flow: {reply!r} {actions.log}")
    assert reply == "Opened wikipedia.", reply
    assert [a[:2] for a in actions.log] == [("key", "ctrl+t"), ("key", "ctrl+l"), ("type", "en.wikipedia.org"),
                                            ("key", "enter")]   # a new tab: the page you were on stays
    x, y = actions.log[0][2]
    assert 0 < x < 1366 and 0 < y < 100, (x, y)   # icon at the top of the Firefox window
    assert ctx.focus["page"] == "https://en.wikipedia.org/" and bridge.polls >= 3

    # Firefox not focused -> open_app.ensure_open is used first.
    called = []
    other = dict(_win(0, 0, 800, 600, app_id="kitty.desktop"))
    shell = _FakeShell([other, dict(win, focused=False)])

    def fake_ensure(name):
        called.append(name)
        shell.windows = [dict(win, focused=True), dict(other, focused=False)]
        return {"name": "Firefox"}, False
    real = open_website.open_app.ensure_open
    open_website.open_app.ensure_open = fake_ensure
    try:
        bridge = _FakeBridge()
        actions = _FakeActions(on_enter=bridge.enter)
        open_website.init(shell, actions, None, bridge)
        reply = open_website.open_website(Context(), "best pizza")
    finally:
        open_website.open_app.ensure_open = real
    assert called == ["firefox"] and actions.log[2][:2] == ("type", "best pizza"), (called, actions.log)
    assert reply == "Here are the results for best pizza.", reply

    # already on an empty new-tab page: use it, no extra tab
    bridge = _FakeBridge()
    bridge.state["url"] = "about:newtab"
    actions = _FakeActions(on_enter=bridge.enter)
    open_website.init(_FakeShell([win]), actions, None, bridge)
    open_website.open_website(Context(), "wikipedia")
    assert [a[1] for a in actions.log] == ["ctrl+l", "en.wikipedia.org", "enter"], actions.log
    assert open_website.is_blank("about:home") and not open_website.is_blank("https://meet.google.com/x")
    log("open_website flow ok")


# ---- geometry ----

def _win(x, y, w, h, monitor=0, focused=True, app_id="firefox_firefox.desktop"):
    return {"id": 1, "app_id": app_id, "title": "t", "frame": {"x": x, "y": y, "width": w, "height": h},
            "focused": focused, "minimized": False, "monitor": monitor, "on_active_workspace": True}


MONS = [{"index": 0, "x": 0, "y": 0, "width": 1366, "height": 768, "scale": 1},
        {"index": 1, "x": 0, "y": 768, "width": 1536, "height": 960, "scale": 1.25}]


def _close(a, b, eps=0.6):
    return all(abs(p - q) <= eps for p, q in zip(a, b))


def test_geometry():
    # Scale 1, maximized, 85 px of toolbars on top.
    vp = geometry.viewport(_win(0, 0, 1366, 768), {"inner_width": 1366, "inner_height": 683, "dpr": 1,
                                                   "outer_width": 1366, "outer_height": 768}, MONS)
    assert _close((vp.ox, vp.oy, vp.k), (0, 85, 1)), vp
    assert _close(vp.to_global({"x": 10, "y": 20, "width": 100, "height": 30}), (10, 105, 100, 30))

    # Fractional 1.25 on monitor 1: dpr 1.25 -> k 1. Window at (0, 800).
    vp = geometry.viewport(_win(0, 800, 1536, 928, monitor=1),
                           {"inner_width": 1536, "inner_height": 840, "dpr": 1.25}, MONS)
    assert _close((vp.ox, vp.oy, vp.k), (0, 888, 1)), vp

    # Same, page zoomed to 120%: dpr 1.5, innerWidth shrinks to 1280 CSS px -> k 1.2.
    vp = geometry.viewport(_win(0, 800, 1536, 928, monitor=1),
                           {"inner_width": 1280, "inner_height": 700, "dpr": 1.5}, MONS)
    assert _close((vp.ox, vp.oy, vp.k), (0, 888, 1.2)), vp
    assert _close(vp.to_global({"x": 100, "y": 50, "width": 10, "height": 10}), (120, 948, 12, 12))

    # Floating window with 1 px borders on each side.
    vp = geometry.viewport(_win(200, 100, 1002, 700), {"inner_width": 1000, "inner_height": 600, "dpr": 1}, MONS)
    assert _close((vp.ox, vp.oy), (201, 199)), vp

    # dpr that can't fit the frame (Firefox at buffer scale 2 on a 1.25 monitor): measured instead.
    vp = geometry.viewport(_win(0, 800, 1536, 928, monitor=1),
                           {"inner_width": 1536, "inner_height": 840, "dpr": 2}, MONS)
    assert _close((vp.k, vp.oy), (1, 888)), vp

    # Window choice: focused Firefox, else topmost Firefox; never another app.
    wins = [_win(0, 0, 10, 10, app_id="kitty.desktop"), _win(5, 5, 10, 10, focused=False)]
    assert geometry.firefox_window(wins)["frame"]["x"] == 5
    assert geometry.firefox_window(wins[:1]) is None
    assert vp.clip((-10, 790, 30, 200)) == (0, 888, 20, 102)
    log("geometry ok")


# ---- bridge vs a fake extension ----

class FakeExtension:
    """Answers like background.js. Runs its own event loop in a thread."""

    def __init__(self, token=TOKEN, origin="moz-extension://fake", delay_cmds=()):
        self.token, self.origin, self.delay_cmds = token, origin, delay_cmds
        self.closed_code = None
        self.loop = asyncio.new_event_loop()
        self.ws = None
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def start(self):
        asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    async def _run(self):
        try:
            async with connect(f"ws://127.0.0.1:{PORT}", origin=self.origin) as ws:
                self.ws = ws
                await ws.send(json.dumps({"type": "hello", "token": self.token, "agent": "Firefox/149.0"}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if "id" not in msg:
                        continue
                    if msg["cmd"] in self.delay_cmds:
                        continue   # never answer: tests the timeout
                    if msg["cmd"] == "get_url":
                        reply = {"id": msg["id"], "ok": True, "result": {"url": "https://example.org/",
                                                                        "status": "complete", "load_seq": 1}}
                    elif msg["cmd"] == "echo":
                        reply = {"id": msg["id"], "ok": True, "result": msg["args"]}
                    else:
                        reply = {"id": msg["id"], "ok": False, "error": f"unknown command {msg['cmd']}"}
                    await ws.send(json.dumps(reply))
        except Exception:
            pass
        finally:
            self.closed_code = self.ws.close_code if self.ws else -1

    def disconnect(self):
        asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop).result(2)


def _wait(pred, s=3):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_bridge():
    b = Bridge(port=PORT, token=TOKEN)
    assert b.start()
    try:
        try:
            b.call("get_url")
            raise AssertionError("call without extension should fail")
        except BridgeError as e:
            log(f"no extension -> {e}")

        bad = FakeExtension(token="wrong")
        bad.start()
        assert _wait(lambda: bad.closed_code is not None) and bad.closed_code == 4001, bad.closed_code
        assert not b.connected
        evil = FakeExtension(origin="https://evil.example")
        evil.start()
        assert _wait(lambda: evil.closed_code is not None) and evil.closed_code == 4003, evil.closed_code
        assert not b.connected
        log("wrong token / web origin rejected")

        ext = FakeExtension(delay_cmds=("slow",))
        ext.start()
        assert b.wait_connected(3)
        assert b.call("get_url")["url"] == "https://example.org/"
        assert b.call("echo", n=3, s="x") == {"n": 3, "s": "x"}
        # Concurrent calls from several threads get their own replies.
        out = {}
        ts = [threading.Thread(target=lambda i=i: out.__setitem__(i, b.call("echo", i=i)["i"])) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert out == {i: i for i in range(20)}, out
        try:
            b.call("nope")
            raise AssertionError("error reply should raise")
        except BridgeError as e:
            assert "unknown command" in str(e)
        t = time.monotonic()
        try:
            b.call("slow", timeout=0.3)
            raise AssertionError("slow call should time out")
        except BridgeError as e:
            assert "timed out" in str(e) and time.monotonic() - t < 1.0
        log("request/response, errors, timeout ok")

        # Disconnect -> calls fail; the extension reconnects -> calls work again.
        ext.disconnect()
        assert _wait(lambda: not b.connected)
        try:
            b.call("get_url")
            raise AssertionError("call after disconnect should fail")
        except BridgeError:
            pass
        ext2 = FakeExtension()
        ext2.start()
        assert b.wait_connected(3)
        assert b.call("echo", ok=1) == {"ok": 1}
        # A second connection replaces the first.
        ext3 = FakeExtension()
        ext3.start()
        assert _wait(lambda: ext2.closed_code == 4000), ext2.closed_code
        assert b.call("echo", who=3) == {"who": 3}
        log("disconnect, reconnect, replace ok")
    finally:
        b.stop()


if __name__ == "__main__":
    test_resolve_site()
    test_open_website_flow()
    test_geometry()
    test_bridge()
    print("[KIKU] test: browser all passed")
