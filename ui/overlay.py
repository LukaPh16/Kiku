"""Python client for the kiku-shell overlay (org.kiku.Shell on the session bus).

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


def _log(msg):
    print(f"[KIKU] overlay: {msg}", flush=True)


class Overlay:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._bus = None
        self._hotkey_cbs = []
        self._cb_queue = queue.Queue()
        self._warned_at = 0.0
        self._thread = threading.Thread(target=self._loop.run_forever, name="kiku-dbus", daemon=True)
        self._thread.start()
        threading.Thread(target=self._run_callbacks, name="kiku-hotkeys", daemon=True).start()
        self._submit(self._connect(), timeout=5)

    # ---- plumbing ----

    async def _connect(self):
        try:
            self._bus = await MessageBus().connect()
        except Exception as e:
            _log(f"WARNING: no session bus ({e}); overlay disabled")
            return
        rule = f"type='signal',interface='{config.DBUS_IFACE}',member='Hotkey'"
        await self._bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                     interface="org.freedesktop.DBus", member="AddMatch",
                                     signature="s", body=[rule]))
        self._bus.add_message_handler(self._on_message)

    def _on_message(self, msg):
        if (msg.message_type == MessageType.SIGNAL and msg.interface == config.DBUS_IFACE
                and msg.member == "Hotkey"):
            self._cb_queue.put(msg.body[0])

    def _run_callbacks(self):
        # Hotkey callbacks run here, not on the bus thread, so they may call the overlay.
        while True:
            name = self._cb_queue.get()
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

    def move_to(self, x, y, wait=True):
        """Ease Kiku's icon next to (x, y). With wait=True returns once it arrived."""
        self._do("MoveTo", "dd", (float(x), float(y)), wait, ms=400)

    def hover(self, x, y, ms, wait=True):
        """Move to (x, y) and stay for ms."""
        self._do("Hover", "ddu", (float(x), float(y), int(ms)), wait, ms=400 + ms)

    def follow_pointer(self, on):
        """Shell-side: icon tracks the mouse every frame until off, move_to() or panic."""
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
        """Small label under the icon; "" hides it."""
        self._do("ShowCaption", "s", (text,))

    def get_pointer(self):
        """(x, y) of the mouse pointer, or None."""
        r = self._do("GetPointer")
        return (r[0], r[1]) if r else None

    def get_monitors(self):
        """[{index, x, y, width, height, primary, scale, ui_scale}, ...] or []."""
        r = self._do("GetMonitors")
        return json.loads(r[0]) if r else []

    def close(self):
        if self._bus is not None:
            self._loop.call_soon_threadsafe(self._bus.disconnect)
        self._loop.call_soon_threadsafe(self._loop.stop)
