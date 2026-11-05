"""tools/close.py with a fake shell, actions and bridge. No GNOME or Firefox needed.

  venv/bin/python -m tests.test_close
"""
from brain.context import Context
from control.actions import Failed
from tools import close as tool

FF, FILES, EDIT = "firefox_firefox.desktop", "org.gnome.Nautilus.desktop", "org.gnome.TextEditor.desktop"


def win(i, app, title, focused=False):
    return {"id": i, "app_id": app, "title": title, "focused": focused, "minimized": False,
            "on_active_workspace": True, "frame": {"x": 0, "y": 0, "width": 800, "height": 600}}


class Shell:
    def __init__(self, old=False, asks=()):
        self.old, self.asks, self.log = old, set(asks), []
        # Firefox window 1 has 2 tabs (Wikipedia showing, YouTube behind); window 2 shows GitHub
        self.tabs = {1: [(11, "Wikipedia", "https://en.wikipedia.org/wiki/X"),
                         (12, "YouTube", "https://www.youtube.com/")],
                     2: [(21, "GitHub", "https://github.com/")]}
        self.active = {1: 0, 2: 0}
        self.wins = {1: win(1, FF, ""), 2: win(2, FF, ""), 3: win(3, FILES, "Home", focused=True),
                     4: win(4, EDIT, "notes.txt")}

    def list_windows(self):
        for i, tabs in self.tabs.items():
            if i in self.wins:
                self.wins[i]["title"] = f"{tabs[self.active[i]][1]} — Mozilla Firefox"
        return [dict(w) for w in self.wins.values()]

    def list_apps(self):
        return [{"app_id": FF, "name": "Firefox", "exec": "firefox", "generic": "Web Browser", "running": True},
                {"app_id": FILES, "name": "Files", "exec": "nautilus", "generic": "", "running": True},
                {"app_id": EDIT, "name": "Text Editor", "exec": "gnome-text-editor", "generic": "", "running": True},
                {"app_id": "org.gnome.Calculator.desktop", "name": "Calculator", "exec": "gnome-calculator",
                 "generic": "", "running": False}]

    def close_window(self, i):
        self.log.append(("close_window", i))
        if self.old:
            return None
        if i not in self.asks:
            self.wins.pop(i, None)
        return True

    def activate_window(self, i):
        for w in self.wins.values():
            w["focused"] = w["id"] == i
        return True

    def focused(self):
        return next(w for w in self.wins.values() if w["focused"])

    def key(self, combo):
        self.log.append(("key", combo))
        w = self.focused()
        if combo == "alt+F4":
            self.wins.pop(w["id"])
        elif combo == "ctrl+w":
            tabs = self.tabs[w["id"]]
            tabs.pop(self.active[w["id"]])
            if not tabs:
                self.wins.pop(w["id"])
                del self.tabs[w["id"]]
            else:
                self.active[w["id"]] = 0

    def clear_highlight(self):
        pass


class Actions:
    def __init__(self, shell):
        self.shell = shell

    def begin(self):
        pass

    def point_at(self, target):
        self.shell.log.append(("point", getattr(target, "label", target)))

    def key(self, combo, target=None):
        self.shell.key(combo)

    def wait(self, s):
        pass


class Bridge:
    def __init__(self, shell, connected=True):
        self.shell, self.connected = shell, connected

    def call(self, cmd, **args):
        s = self.shell
        if cmd == "find_tabs":
            return [{"tab_id": t, "title": title, "url": url}
                    for tabs in s.tabs.values() for t, title, url in tabs]
        if cmd == "activate_tab":
            for w, tabs in s.tabs.items():
                for k, (t, _, _) in enumerate(tabs):
                    if t == args["tab_id"]:
                        s.active[w] = k
            return {}
        if cmd == "get_url":
            w = s.focused()["id"]
            return {"tab_id": s.tabs[w][s.active[w]][0]}


def setup(**kw):
    connected = kw.pop("connected", True)
    shell = Shell(**kw)
    tool.init(shell, Actions(shell), Bridge(shell, connected))
    return shell


def run(target=None):
    return tool.close(Context(), target)


def test_focused_window():
    s = setup()
    assert run() == "Closed Files.", s.log
    assert 3 not in s.wins and ("close_window", 3) in s.log
    assert s.log[0][0] == "point"   # the icon goes to the title bar first


def test_app_by_name():
    s = setup()
    assert run("firefox") == "Closed Firefox."
    assert not any(s.wins[i]["app_id"] == FF for i in s.wins)
    assert run("text editor") == "Closed Text Editor."


def test_app_asks_first():
    s = setup(asks={4})
    assert run("text editor") == "Text Editor is asking something before it closes."


def test_old_shell_uses_alt_f4():
    s = setup(old=True)
    assert run("text editor") == "Closed Text Editor.", s.log
    assert ("key", "alt+F4") in s.log


def test_current_tab():
    s = setup()
    assert run("tab") == "Closed Wikipedia.", s.log   # topmost Firefox window, its tab
    assert [t[0] for t in s.tabs[1]] == [12] and 1 in s.wins


def test_named_tab_in_background():
    s = setup()
    assert run("youtube tab") == "Closed YouTube.", s.log
    assert [t[0] for t in s.tabs[1]] == [11]
    assert run("github") == "Closed GitHub."   # last tab: the window closes too
    assert 2 not in s.wins


def test_named_tab_without_bridge():
    s = setup(connected=False)
    assert run("youtube") != "Closed YouTube."   # behind another tab: can't be seen without the bridge
    assert run("github tab") == "Closed GitHub."


def test_not_found():
    s = setup()
    got = run("spotify")
    assert got == "Sorry, I couldn't find a window or tab called spotify.", got
    assert run("calculator").startswith("Sorry, I couldn't find")   # installed, but not open
    assert not any(e[0] in ("key", "close_window") for e in s.log), s.log


def test_no_firefox_for_tab():
    s = setup()
    s.wins = {3: win(3, FILES, "Home", focused=True)}
    assert run("tab") == "Sorry, Firefox isn't open."


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"[KIKU] test: ok  {name}")
            except (AssertionError, Failed) as e:
                fails += 1
                print(f"[KIKU] test: BAD {name}: {e!r}")
    print(f"[KIKU] test: close {'all passed' if not fails else f'{fails} failed'}")
    sys.exit(fails != 0)
