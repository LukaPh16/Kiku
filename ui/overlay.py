"""Python client for kiku-shell (org.kiku.Shell on the session bus): overlay, desktop info, input.

Same methods as the D-Bus API, in snake_case. All coordinates are global stage
(logical) pixels, passed through unchanged; scaling is handled in the extension.

Thread-safe: the bus runs on its own asyncio thread and every call is handed to it.
If kiku-shell isn't running, calls log a warning and return a default instead of raising.
"""
import asyncio
import json
import queue
import threading
import time

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

import config

_RETRY_S = 5.0   # don't spam warnings while the extension is missing
_RESHOW = object()   # callback-queue item: kiku-shell came back on the bus


def _log(msg):
    print(f"[KIKU] overlay: {msg}", flush=True)


class Overlay:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._bus = None
        self._hotkey_cbs = []
        self._cb_queue = queue.Queue()
        self._warned_at = 0.0
        self.at = None   # (x, y) the icon was last sent to; None after home/show/abort/follow
        self._shown = False   # we called Show (re-sent when kiku-shell comes back, e.g. after unlock)
        self._configured = None   # last Configure result (None = not tried)
        self._thread = threading.Thread(target=self._loop.run_forever, name="kiku-dbus", daemon=True)
        self._thread.start()
        self._cb_thread = threading.Thread(target=self._run_callbacks, name="kiku-hotkeys", daemon=True)
        self._cb_thread.start()
        self._submit(self._connect(), timeout=5)

    # ---- plumbing ----

    async def _connect(self):
        try:
            self._bus = await MessageBus().connect()
        except Exception as e:
            _log(f"WARNING: no session bus ({e}); overlay disabled")
            return
        # GNOME disables extensions while the screen is locked: kiku-shell leaves the bus and comes back
        # hidden. NameOwnerChanged tells us to show Kiku again.
        rules = [f"type='signal',interface='{config.DBUS_IFACE}',member='Hotkey'",
                 f"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{config.DBUS_NAME}'"]
        for rule in rules:
            await self._bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                         interface="org.freedesktop.DBus", member="AddMatch",
                                         signature="s", body=[rule]))
        self._bus.add_message_handler(self._on_message)

    def _on_message(self, msg):
        if (msg.message_type == MessageType.SIGNAL and msg.member == "NameOwnerChanged"
                and msg.body and msg.body[0] == config.DBUS_NAME):
            if msg.body[2]:
                self._cb_queue.put(_RESHOW)
            else:
                self.at = None
                _log("kiku-shell left the bus (screen locked or shell restarting)")
            return
        if (msg.message_type == MessageType.SIGNAL and msg.interface == config.DBUS_IFACE
                and msg.member == "Hotkey"):
            if msg.body[0] == "panic":
                self.at = None   # the shell stopped the icon wherever it was
            self._cb_queue.put(msg.body[0])

    def _run_callbacks(self):
        # Hotkey callbacks run here, not on the bus thread, so they may call the overlay.
        while True:
            name = self._cb_queue.get()
            if name is None:   # close()
                return
            if name is _RESHOW:
                if self._shown:
                    _log("kiku-shell is back; showing Kiku again")
                    self.show()
                continue
            for cb in list(self._hotkey_cbs):
                try:
                    cb(name)
                except Exception as e:
                    _log(f"hotkey callback failed: {e}")

    def _submit(self, coro, timeout):
        if threading.current_thread() is self._thread:
            raise RuntimeError("Overlay methods can't be called from the D-Bus thread")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout)
        except Exception as e:
            fut.cancel()
            _log(f"WARNING: call failed: {e!r}")
            return None

    async def _call(self, member, signature="", body=()):
        if self._bus is None:
            return None
        reply = await self._bus.call(Message(destination=config.DBUS_NAME, path=config.DBUS_PATH,
                                             interface=config.DBUS_IFACE, member=member,
                                             signature=signature, body=list(body)))
        if reply.message_type == MessageType.ERROR:
            now = time.monotonic()
            if now - self._warned_at > _RETRY_S:
                self._warned_at = now
                hint = " (is kiku-shell enabled?)" if "ServiceUnknown" in reply.error_name else ""
                _log(f"WARNING: {member}: {reply.error_name}: {reply.body[0] if reply.body else ''}{hint}")
            return None
        return reply.body

    def _do(self, member, signature="", body=(), wait=True, ms=0):
        coro = self._call(member, signature, body)
        if not wait:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return None
        return self._submit(coro, timeout=5 + ms / 1000)

    @property
    def available(self):
        """True if kiku-shell answers right now."""
        return self.get_monitors() != []

    def on_hotkey(self, callback):
        """callback(name) for each Hotkey signal ("panic", "ptt"). Runs on a helper thread."""
        self._hotkey_cbs.append(callback)

    # ---- API (mirrors org.kiku.Shell) ----

    def show(self):
        """Make Kiku visible (with config.py's look). The shell hides it again on hide() or when this
        process exits."""
        self.at = None
        self._shown = True
        self._do("Show")
        self.configure()

    def configure(self):
        """Send config.OVERLAY_* to kiku-shell. Captions are handled here (show_caption)."""
        speed = max(0.1, float(config.OVERLAY_SPEED))
        cfg = {"icon_size": int(config.OVERLAY_ICON_SIZE),
               "move_min_ms": round(config.MOVE_MIN_MS / speed), "move_max_ms": round(config.MOVE_MAX_MS / speed)}
        ok = self._do("Configure", "s", (json.dumps(cfg),)) is not None
        if not ok and self._configured is None:
            _log("kiku-shell doesn't take visual settings yet (old version loaded): log out and back in")
        self._configured = ok

    def hide(self):
        self.at = None
        self._shown = False
        self._do("Hide")

    def move_to(self, x, y, wait=True):
        """Ease Kiku's icon next to (x, y). With wait=True returns once it arrived."""
        self.at = (float(x), float(y))
        self._do("MoveTo", "dd", (float(x), float(y)), wait, ms=400)

    def go_home(self, wait=True):
        """Ease Kiku's icon back to its start spot (bottom-right of the primary monitor)."""
        self.at = None
        self._do("GoHome", wait=wait, ms=400)

    def hover(self, x, y, ms, wait=True):
        """Move to (x, y) and stay for ms."""
        self.at = (float(x), float(y))
        self._do("Hover", "ddu", (float(x), float(y), int(ms)), wait, ms=400 + ms)

    def follow_pointer(self, on):
        """Shell-side: icon tracks the mouse every frame until off, move_to() or panic."""
        self.at = None
        self._do("FollowPointer", "b", (bool(on),))

    def click_pulse(self):
        self._do("ClickPulse")

    def set_state(self, state):
        """idle | listen | think | talk"""
        self._do("SetState", "s", (state,))

    def highlight_rect(self, x, y, w, h):
        self._do("HighlightRect", "dddd", (float(x), float(y), float(w), float(h)))

    def clear_highlight(self):
        self._do("ClearHighlight")

    def scan_rect(self, x, y, w, h, ms, wait=True):
        self._do("ScanRect", "ddddu", (float(x), float(y), float(w), float(h), int(ms)), wait, ms=ms)

    def show_caption(self, text):
        """Small label under the icon; "" hides it. Off with config.OVERLAY_CAPTIONS = False."""
        if text and not config.OVERLAY_CAPTIONS:
            return
        self._do("ShowCaption", "s", (text,))

    def get_pointer(self):
        """(x, y) of the mouse pointer, or None."""
        r = self._do("GetPointer")
        return (r[0], r[1]) if r else None

    def get_monitors(self):
        """[{index, x, y, width, height, primary, scale, ui_scale}, ...] or []."""
        r = self._do("GetMonitors")
        return json.loads(r[0]) if r else []

    # ---- desktop (read-only) ----

    def _json(self, member, default):
        r = self._do(member)
        return json.loads(r[0]) if r else default

    def list_windows(self):
        """[{id, app_id, title, frame, buffer, focused, minimized, monitor, on_active_workspace, pid}], top first."""
        return self._json("ListWindows", [])

    def list_apps(self):
        """[{app_id, name, generic, keywords, exec, running}]"""
        return self._json("ListApps", [])

    def dash_icons(self):
        """[{app_id, name, rect, running, visible}] from the dock (or overview dash)."""
        return self._json("DashIcons", [])

    def overview_state(self):
        """{visible, animating, search_rect, search_focused, search_text}"""
        return self._json("OverviewState", {})

    def activate_window(self, window_id):
        r = self._do("ActivateWindow", "t", (int(window_id),))
        return bool(r and r[0])

    def launch_app(self, app_id):
        r = self._do("LaunchApp", "s", (app_id,))
        return bool(r and r[0])

    def screenshot(self):
        """Path of a full-stage PNG in /tmp, or None."""
        r = self._do("Screenshot", ms=3000)
        return r[0] if r else None

    # ---- input (virtual devices). Return False if aborted/failed. ----

    def pointer_move(self, x, y):
        self._do("PointerMove", "dd", (float(x), float(y)))

    def click(self, button=1):
        r = self._do("Click", "u", (int(button),))
        return bool(r and r[0])

    def scroll(self, dx, dy):
        r = self._do("Scroll", "ii", (int(dx), int(dy)), ms=40 * (abs(dx) + abs(dy)))
        return bool(r and r[0])

    def key_combo(self, combo):
        """"ctrl+l", "super", "enter", "ctrl+shift+t"."""
        r = self._do("KeyCombo", "s", (combo,))
        return bool(r and r[0])

    def type_text(self, text, cps):
        """Returns chars that couldn't be typed (0 = all), -1 if aborted, None if the call failed."""
        r = self._do("TypeText", "su", (text, int(cps)), ms=len(text) * 1000 // max(1, int(cps)))
        return r[0] if r else None

    def abort(self):
        """Same as the panic hotkey: stop motion and input, release held keys."""
        self.at = None
        self._do("Abort")

    def close(self):
        """Disconnect and stop the bus and callback threads. Call last."""
        self._cb_queue.put(None)
        if self._bus is not None:
            self._loop.call_soon_threadsafe(self._bus.disconnect)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(2)
        if threading.current_thread() is not self._cb_thread:
            self._cb_thread.join(2)
