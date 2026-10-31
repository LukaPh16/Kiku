"""Overlay demo.

  python demo_overlay.py           scripted tour of every monitor
  python demo_overlay.py --follow  icon follows the mouse (Ctrl+C to quit)

Hotkey signals (panic / ptt) are printed as they arrive. Panic stops the tour.
"""
import argparse
import threading
import time

from ui.overlay import Overlay

INSET = 40   # keep corner targets this far inside the monitor edge
panic = threading.Event()


def log(msg):
    print(f"[KIKU] demo: {msg}", flush=True)


def on_hotkey(name):
    log(f"Hotkey signal: {name}")
    if name == "panic":
        panic.set()


def step(ov, caption):
    if panic.is_set():
        raise KeyboardInterrupt
    log(caption)
    ov.show_caption(caption)


def tour(ov):
    monitors = ov.get_monitors()
    for m in monitors:
        x, y, w, h = m["x"], m["y"], m["width"], m["height"]
        log(f"monitor {m['index']}: {w}x{h}+{x}+{y} scale={m['scale']} primary={m['primary']}")
        corners = [(x + INSET, y + INSET), (x + w - INSET, y + INSET),
                   (x + w - INSET, y + h - INSET), (x + INSET, y + h - INSET)]
        for i, (cx, cy) in enumerate(corners):
            step(ov, f"corner {i + 1}")
            ov.move_to(cx, cy)
            ov.click_pulse()
            time.sleep(0.4)

        cx, cy = x + w // 2, y + h // 2
        step(ov, "hover")
        ov.hover(cx, cy, 800)

        rx, ry, rw, rh = cx - 300, cy - 150, 600, 300
        step(ov, "highlight")
        ov.move_to(rx + rw, ry)
        ov.highlight_rect(rx, ry, rw, rh)
        time.sleep(1.2)
        ov.clear_highlight()

        step(ov, "scan")
        ov.scan_rect(rx, ry, rw, rh, 1200)

        for state in ("listen", "think", "talk", "idle"):
            step(ov, f"state: {state}")
            ov.set_state(state)
            time.sleep(1.8)
    ov.show_caption("")
    log("tour done")


def follow(ov):
    log("icon follows the mouse (tracked in the shell), Ctrl+C or panic to stop")
    ov.follow_pointer(True)
    panic.wait()
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--follow", action="store_true", help="follow the mouse pointer")
    args = ap.parse_args()

    ov = Overlay()
    ov.on_hotkey(on_hotkey)
    if not ov.available:
        log("kiku-shell is not running; nothing to show")
        return
    try:
        follow(ov) if args.follow else tour(ov)
    except KeyboardInterrupt:
        log("stopped")
    finally:
        ov.follow_pointer(False)
        ov.show_caption("")
        ov.set_state("idle")
        ov.clear_highlight()
        ov.close()


if __name__ == "__main__":
    main()
