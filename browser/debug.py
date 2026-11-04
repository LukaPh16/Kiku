"""Check page -> screen coordinates against the overlay: outlines elements with highlight_rect.

  venv/bin/python -m browser.debug                     # outline the viewport + the first paragraphs
  venv/bin/python -m browser.debug --text "Sign in"    # outline elements by visible text
  venv/bin/python -m browser.debug --selector "a" --limit 5
  venv/bin/python -m browser.debug --role link
  venv/bin/python -m browser.debug --select 0 10 40    # select chars 10..40 of paragraph 0

Each rect is outlined for --ms, one after another. The outline should sit exactly on the element.
"""
import argparse
import time

from browser import geometry
from browser.bridge import Bridge
from browser.page import Page
from ui.overlay import Overlay


def log(msg):
    print(f"[KIKU] browser.debug: {msg}", flush=True)


def show(ov, name, rect, ms):
    x, y, w, h = rect
    log(f"{name}: global ({x:.0f},{y:.0f} {w:.0f}x{h:.0f})")
    ov.highlight_rect(x, y, w, h)
    time.sleep(ms / 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--selector")
    ap.add_argument("--role")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--select", nargs=3, type=int, metavar=("PARA", "START", "END"))
    ap.add_argument("--ms", type=int, default=1500)
    ap.add_argument("--wait", type=float, default=30, help="seconds to wait for the extension")
    args = ap.parse_args()

    ov = Overlay()
    if not ov.available:
        log("kiku-shell is not running")
        return 1
    bridge = Bridge()
    if not bridge.start():
        return 1
    log("waiting for the Firefox extension...")
    if not bridge.wait_connected(args.wait):
        log("extension didn't connect")
        return 1
    page = Page(bridge, ov)
    try:
        info = page.url()
        log(f"tab: {info['title']!r} {info['url']} ({info['status']})")
        view = bridge.call("get_viewport")["view"]
        vp = page.viewport(view)
        win = vp.window
        f = win["frame"]
        log(f"window {win['title']!r} frame ({f['x']},{f['y']} {f['width']}x{f['height']}) "
            f"monitor {win['monitor']} scale {geometry.monitor_scale(win, ov.get_monitors())}")
        log(f"view: inner {view['inner_width']}x{view['inner_height']} outer {view['outer_width']}x"
            f"{view['outer_height']} dpr {view['dpr']} screenX/Y {view['screen_x']},{view['screen_y']}")
        log(f"k {vp.k:.3f}; chrome from frame {vp.oy - f['y']:.0f} px top, {vp.ox - f['x']:.0f} px sides; "
            f"outer-inner says {(view['outer_height'] - view['inner_height']) * vp.k:.0f} px x "
            f"{(view['outer_width'] - view['inner_width']) * vp.k:.0f} px")
        show(ov, "viewport", (vp.ox, vp.oy, vp.width, vp.height), args.ms)

        if args.select:
            n, start, end = args.select
            paras = page.paragraphs()
            el = page.scroll_into_view(paras[n])
            sel = page.select(el, start, end)
            log(f"selected {sel['text']!r}")
            for t in sel["targets"]:
                show(ov, "selection", t.rect, args.ms)
            page.clear_selection()
        elif args.text or args.selector or args.role:
            els = page.find(selector=args.selector, role=args.role, text=args.text, limit=args.limit)
            log(f"{len(els)} elements")
            for el in els:
                page.scroll_into_view(el)   # fresh rect (an earlier scroll may have moved it)
                show(ov, f"{el['id']} <{el['tag']}> {el['text'][:50]!r}", el["target"].rect, args.ms)
        else:
            for el in [p for p in page.paragraphs() if p["in_view"]][:args.limit]:
                show(ov, f"{el['id']} paragraph {el['text'][:50]!r}", el["target"].rect, args.ms)
    finally:
        ov.clear_highlight()
        bridge.stop()
        ov.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
