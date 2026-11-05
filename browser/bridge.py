"""WebSocket server the Kiku Firefox extension connects to (ws://127.0.0.1:BROWSER_PORT).

Runs asyncio in its own thread. The extension connects (and reconnects by itself), sends
{"type": "hello", "token": ...}; we answer {"type": "welcome"} or close with 4001.
Requests: {"id", "cmd", "args"} -> {"id", "ok", "result" | "error"}.

  bridge = Bridge(); bridge.start()
  bridge.call("get_url")                       # thread-safe, raises BridgeError
  bridge.call("get_elements", text="Sign in")

  venv/bin/python -m browser.bridge --token    # port + token for the extension's options page
  venv/bin/python -m browser.bridge            # wait for the extension, print the current tab
"""
import asyncio
import hmac
import itertools
import json
import threading

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import config


def _log(msg):
    print(f"[KIKU] bridge: {msg}", flush=True)


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, host=None, port=None, token=None):
        self.host = host or config.BROWSER_HOST
        self.port = port or config.BROWSER_PORT
        self.token = token or config.BROWSER_TOKEN
        self._loop = asyncio.new_event_loop()
        self._ws = None              # the connected extension (only one at a time)
        self._pending = {}           # id -> asyncio.Future
        self._ids = itertools.count(1)
        self._connected = threading.Event()
        self._server = None
        self._thread = None
        # () -> title of the Firefox window that has focus (main.py sets it from kiku-shell). The
        # extension uses it to pick the right tab when there are several Firefox windows.
        self.window_title = None

    # ---- lifecycle ----

    def start(self):
        """Start listening. Returns False if the port can't be opened."""
        self._thread = threading.Thread(target=self._loop.run_forever, name="kiku-bridge", daemon=True)
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._serve(), self._loop).result(5)
        except Exception as e:
            _log(f"WARNING: can't listen on {self.host}:{self.port} ({e}); browser control disabled")
            return False
        _log(f"listening on ws://{self.host}:{self.port}")
        return True

    async def _serve(self):
        self._server = await serve(self._handler, self.host, self.port, max_size=8 * 2**20,
                                   ping_interval=20, ping_timeout=20)

    def stop(self):
        """Close the extension connection and the server, end the thread. Safe to call twice."""
        async def _close():
            if self._server is not None:
                self._server.close()   # also closes the extension's connection (it reconnects later)
                await self._server.wait_closed()
        if self._thread and self._thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(_close(), self._loop).result(3)
            except Exception as e:
                _log(f"close: {e!r}")
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(2)
            _log("stopped")

    @property
    def connected(self):
        return self._connected.is_set()

    def wait_connected(self, timeout):
        return self._connected.wait(timeout)

    # ---- connection ----

    async def _handler(self, ws):
        peer = ws.remote_address
        origin = ws.request.headers.get("Origin", "")
        if not origin.startswith("moz-extension://"):
            _log(f"rejected {peer}: origin {origin!r} is not a Firefox extension")
            await ws.close(4003, "bad origin")
            return
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        except (asyncio.TimeoutError, ConnectionClosed, ValueError, TypeError):
            await ws.close(4002, "no hello")
            return
        if (not isinstance(hello, dict) or hello.get("type") != "hello"
                or not hmac.compare_digest(str(hello.get("token", "")), self.token)):
            _log(f"rejected {peer}: wrong token (paste the token from `python -m browser.bridge --token`)")
            await ws.close(4001, "bad token")
            return
        old, self._ws = self._ws, ws
        if old is not None:
            await old.close(4000, "replaced")
        await ws.send(json.dumps({"type": "welcome"}))
        self._connected.set()
        _log(f"extension connected ({_short_agent(hello.get('agent', ''))})")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                fut = self._pending.pop(msg.get("id"), None) if isinstance(msg, dict) else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except ConnectionClosed:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
                self._connected.clear()
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(BridgeError("extension disconnected"))
                self._pending.clear()
                _log("extension disconnected")

    # ---- requests ----

    async def _request(self, cmd, args, timeout):
        ws = self._ws
        if ws is None:
            raise BridgeError("Firefox extension not connected")
        rid = next(self._ids)
        fut = self._loop.create_future()
        self._pending[rid] = fut
        try:
            await ws.send(json.dumps({"id": rid, "cmd": cmd, "args": args}))
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise BridgeError(f"{cmd} timed out after {timeout:.1f}s") from None
        except ConnectionClosed:
            raise BridgeError("extension disconnected") from None
        finally:
            self._pending.pop(rid, None)
        if not msg.get("ok"):
            raise BridgeError(f"{cmd}: {msg.get('error', 'failed')}")
        return msg.get("result")

    def call(self, cmd, timeout=None, **args):
        """Send one command to the extension and wait for its result. Raises BridgeError."""
        if self._thread is None or threading.current_thread() is self._thread:
            raise BridgeError("bridge not started (or called from its own thread)")
        timeout = config.BROWSER_CALL_TIMEOUT_S if timeout is None else timeout
        if self.window_title is not None:
            try:
                title = self.window_title()
            except Exception as e:
                _log(f"window title lookup failed: {e!r}")
                title = None
            if title:
                args["_window_title"] = title
        fut = asyncio.run_coroutine_threadsafe(self._request(cmd, args, timeout), self._loop)
        try:
            return fut.result(timeout + 1)
        except BridgeError:
            raise
        except Exception as e:
            fut.cancel()
            raise BridgeError(f"{cmd} failed: {e!r}") from None


def _short_agent(ua):
    return next((p for p in ua.split() if p.startswith("Firefox/")), ua[:40] or "unknown")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", action="store_true", help="print port and token for the extension options")
    ap.add_argument("--wait", type=float, default=30, help="seconds to wait for the extension")
    args = ap.parse_args()
    if args.token:
        print(f"port:  {config.BROWSER_PORT}\ntoken: {config.BROWSER_TOKEN}")
        return 0
    b = Bridge()
    if not b.start():
        return 1
    _log(f"waiting up to {args.wait:.0f}s for the Firefox extension...")
    if not b.wait_connected(args.wait):
        _log("no extension connected (is it loaded, with this port and token?)")
        return 1
    print(json.dumps(b.call("get_url"), indent=2))
    b.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
