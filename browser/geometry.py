"""Page rects (viewport CSS px from the extension) -> global stage (logical) px.

On Wayland window.screenX/Y is useless (often 0), so the content origin is worked out from:
  - the Firefox window frame from kiku-shell ListWindows (global logical px),
  - the viewport size from the extension (innerWidth/innerHeight, CSS px),
  - CSS px -> logical px: k = devicePixelRatio / monitor scale (dpr already includes page zoom).
The chrome around the content is frame size - inner size * k (the same as outerWidth - innerWidth,
but in logical px, so a CSD shadow counted in outerWidth can't skew it). Toolbars are assumed to be
on top: the side chrome is split evenly left/right, the bottom gets the same border as the sides,
and the rest is above the content. A sidebar, the find bar or docked devtools break that.

Pure functions, no D-Bus: tests/test_browser.py runs them on fixed numbers.
"""
from dataclasses import dataclass

from control.locate import Target


def _log(msg):
    print(f"[KIKU] geometry: {msg}", flush=True)


@dataclass
class Viewport:
    """Where the page viewport sits on screen: global = (ox + css_x * k, oy + css_y * k)."""
    ox: float
    oy: float
    k: float
    width: float     # viewport size in logical px
    height: float
    window: dict

    def to_global(self, r):
        """{x, y, width, height} in viewport CSS px -> (x, y, w, h) in global logical px."""
        return (self.ox + r["x"] * self.k, self.oy + r["y"] * self.k, r["width"] * self.k, r["height"] * self.k)

    def target(self, name, r, info=None):
        return Target(name, *self.to_global(r), "web", 1.0, info or {})

    def clip(self, rect):
        """Part of a global rect inside the viewport, or None."""
        x, y, w, h = rect
        x0, y0 = max(x, self.ox), max(y, self.oy)
        x1, y1 = min(x + w, self.ox + self.width), min(y + h, self.oy + self.height)
        return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def is_firefox(w):
    return "firefox" in (w.get("app_id") or "").lower()


def firefox_window(windows):
    """The focused Firefox window, else the topmost visible one, else None."""
    ff = [w for w in windows if is_firefox(w) and not w["minimized"] and w["on_active_workspace"]]
    return next((w for w in ff if w["focused"]), ff[0] if ff else None)


def monitor_scale(window, monitors):
    m = next((m for m in monitors if m["index"] == window.get("monitor")), None)
    return float(m["scale"]) if m and m.get("scale") else 1.0


def viewport(window, view, monitors):
    """Viewport for `view` (the extension's get_viewport result) in `window` (a ListWindows dict)."""
    f = window["frame"]
    iw, ih = float(view["inner_width"]), float(view["inner_height"])
    scale = monitor_scale(window, monitors)
    k = float(view["dpr"]) / scale
    # Sanity check: the content can't be wider than the window, and side chrome is only a few px.
    # If dpr/scale doesn't fit (e.g. Firefox rendering at an integer buffer scale), measure instead.
    if iw > 0 and not (-2 <= f["width"] - iw * k <= 0.15 * f["width"]):
        k_meas = f["width"] / iw
        _log(f"WARNING: dpr {view['dpr']} / scale {scale} = {k:.3f} doesn't fit the {f['width']} px "
             f"frame (inner {iw:.0f} CSS px); using {k_meas:.3f} instead")
        k = k_meas
    side = max(0.0, (f["width"] - iw * k) / 2)
    top = max(0.0, f["height"] - ih * k - side)
    return Viewport(f["x"] + side, f["y"] + top, k, iw * k, ih * k, window)
