"""open_app: switch to an app if it's open, otherwise launch it visibly.

Open      -> icon moves to its window (or dock icon), then ActivateWindow.
Not open  -> click its dock icon if pinned and visible, else Super -> type name in the
             Activities search -> Enter. Then wait for the window and say "Opened X".
Spoken names are fuzzy-matched to installed apps; unclear names get a question, not a guess.
"""
import time

import config
from control.actions import Aborted
from control.locate import Target, text_score
from tools.registry import register

_shell = _actions = _locator = None


def _log(msg):
    print(f"[KIKU] open_app: {msg}", flush=True)


def init(shell, actions, locator):
    global _shell, _actions, _locator
    _shell, _actions, _locator = shell, actions, locator


# ---- name matching ----

def app_score(spoken, app):
    stem = app["app_id"].removesuffix(".desktop").split(".")[-1].split("_")[-1]
    scores = [text_score(spoken, app["name"]),
              0.95 * text_score(spoken, app["exec"]),
              0.9 * text_score(spoken, stem),
              0.85 * text_score(spoken, app.get("generic", ""))]
    if any(" ".join(spoken.lower().split()) == k.lower() for k in app.get("keywords", [])):
        scores.append(0.8)
    return max(scores)


def resolve(spoken, apps, pinned=()):
    """Return the app dict, or a string to say back (not found / which one?)."""
    spoken = config.APP_ALIASES.get(spoken.lower().strip(), spoken)
    ranked = sorted(((app_score(spoken, a), a) for a in apps), key=lambda p: -p[0])
    if not ranked or ranked[0][0] < config.APP_MATCH_MIN:
        near = ", ".join(a["name"] for s, a in ranked[:2] if s >= 0.5)
        return f"I couldn't find an app called {spoken}." + (f" Did you mean {near}?" if near else "")
    best_score = ranked[0][0]
    tied = [a for s, a in ranked if best_score - s < 0.03]
    if len(tied) > 1:
        # Break ties by what the user actually uses: running, then pinned in the dock.
        for pick in ([a for a in tied if a["running"]], [a for a in tied if a["app_id"] in pinned]):
            if len(pick) == 1:
                return pick[0]
        names = list(dict.fromkeys(a["name"] for a in tied[:3]))
        if len(names) == 1:
            return f"There are {len(tied)} apps called {names[0]}. Please open it yourself this time."
        return f"Which one do you mean: {' or '.join(names)}?"
    return ranked[0][1]


# ---- the tool ----

@register("open_app", "Open or switch to a desktop application", {"app": {"type": "string"}})
def open_app(ctx, app):
    if _shell is None:
        return "Desktop control isn't set up."
    apps = _shell.list_apps()
    if not apps:
        return "I can't reach kiku-shell, so I can't open apps."
    pinned = {d["app_id"] for d in _shell.dash_icons()}
    match = resolve(app, apps, pinned)
    if isinstance(match, str):
        _log(f"{app!r}: {match}")
        return match
    _log(f"{app!r} -> {match['name']} ({match['app_id']})")
    _actions.begin()
    t = time.monotonic()
    try:
        reply = _open(match)
    except Aborted:
        _log("aborted")
        return "Stopped."
    except RuntimeError as e:
        _log(f"failed: {e}")
        return f"Sorry, {e}."
    finally:
        _shell.clear_highlight()
    _log(f"{reply} ({(time.monotonic() - t) * 1000:.0f} ms)")
    ctx.focus["app"] = match["name"]
    return reply


def ensure_open(spoken):
    """For other tools: open or switch to an app. Returns (app dict, launched: bool).
    Raises RuntimeError(text to say) if it can't; Aborted passes through. Caller calls begin()."""
    apps = _shell.list_apps() if _shell else []
    if not apps:
        raise RuntimeError("I can't reach kiku-shell, so I can't open apps")
    match = resolve(spoken, apps, {d["app_id"] for d in _shell.dash_icons()})
    if isinstance(match, str):
        raise RuntimeError(match)
    reply = _open(match)
    _log(reply)
    return match, reply.startswith("Opened")


def _title_bar(w):
    f = w["frame"]
    return Target(w["title"], f["x"], f["y"], f["width"], min(40, f["height"]), "window", 1.0, {"window": w})


def _open(app):
    name, app_id = app["name"], app["app_id"]
    windows = [w for w in _shell.list_windows() if w["app_id"] == app_id]
    if windows:
        w = windows[0]   # topmost
        visible = not w["minimized"] and w["on_active_workspace"]
        target = _title_bar(w) if visible else _locator.dock_icon(app_id=app_id)
        if target:
            _actions.point_at(target)
        if not _shell.activate_window(w["id"]):
            raise RuntimeError(f"I couldn't switch to {name}")
        return f"Switched to {name}."

    before = {w["id"] for w in _shell.list_windows()}
    icon = _locator.dock_icon(app_id=app_id)
    if icon:
        _log(f"dock icon at {icon.rect}")
        _actions.click(icon)
    else:
        _log("not in the dock; using Activities search")
        _search_launch(name)
    w = _wait_window(app_id, before)
    if w is None:
        raise RuntimeError(f"I started {name}, but no window showed up")
    _actions.point_at(_title_bar(w))
    return f"Opened {name}."


def _search_launch(name):
    mons = _shell.get_monitors()
    m = next((m for m in mons if m["primary"]), mons[0] if mons else None)
    if m is None:
        raise RuntimeError("I can't see any monitor")
    if not _shell.overview_state().get("visible"):
        _actions.key("super", target=(m["x"] + 40, m["y"] + 16))   # Activities corner
        st = _wait_overview()
    else:
        st = _shell.overview_state()
    r = st.get("search_rect")
    search = (Target("search", r["x"], r["y"], r["width"], r["height"], "shell") if r
              else (m["x"] + m["width"] / 2, m["y"] + 60))
    if st.get("search_text"):
        _actions.key("ctrl+a", target=search)
    _actions.type_text(name, target=search)
    # Only press Enter if the text really landed in the Activities search.
    _actions.wait(0.3)   # search results update asynchronously
    st = _shell.overview_state()
    if not st.get("visible") or st.get("search_text", "").strip().lower() != name.lower():
        raise RuntimeError(f"typing into the Activities search didn't work (got {st.get('search_text')!r})")
    _actions.key("enter", target=search)


def _wait_overview():
    end = time.monotonic() + config.OVERVIEW_WAIT_S
    while time.monotonic() < end:
        st = _shell.overview_state()
        if st.get("visible"):
            return st
        _actions.wait(0.05)
    raise RuntimeError("Activities didn't open after the Super key")


def _wait_window(app_id, before):
    end = time.monotonic() + config.APP_WAIT_S
    while time.monotonic() < end:
        for w in _shell.list_windows():
            if w["id"] not in before and w["app_id"] == app_id:
                return w
        _actions.wait(0.15)
    return None
