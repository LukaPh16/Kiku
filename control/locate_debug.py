"""Find something by name and draw the highlight on it.

  venv/bin/python -m control.locate_debug "Firefox"
  venv/bin/python -m control.locate_debug "Save" --source atspi
  venv/bin/python -m control.locate_debug "Settings" --source ocr --hold 5
  venv/bin/python -m control.locate_debug --list      # windows, dock icons, overview state
"""
import argparse
import json
import time

from control.locate import Locator
from ui.overlay import Overlay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--source", choices=["window", "dock", "atspi", "ocr"], action="append",
                    help="only these sources (repeatable); default: all in order")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to keep the highlight")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    shell = Overlay()
    if not shell.available:
        print("[KIKU] locate_debug: kiku-shell is not running")
        return 1
    if args.list or not args.name:
        print(json.dumps({"windows": shell.list_windows(), "dock": shell.dash_icons(),
                          "overview": shell.overview_state(), "monitors": shell.get_monitors()}, indent=1))
        return 0

    loc = Locator(shell)
    focused = next((w for w in shell.list_windows() if w["focused"]), None)
    target = loc.find(args.name, window=focused, sources=tuple(args.source or ("window", "dock", "atspi", "ocr")))
    if target is None:
        return 2
    print(f"[KIKU] locate_debug: {target}  center={tuple(round(c) for c in target.center)}")
    shell.show()
    shell.highlight_rect(*target.rect)
    shell.hover(*target.center, 200)
    time.sleep(args.hold)
    shell.clear_highlight()
    shell.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
