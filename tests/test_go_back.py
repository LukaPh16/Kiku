"""tools/go_back.py ("go back" outside Gmail) and close.front_window, with the fakes from test_close.

  venv/bin/python -m tests.test_go_back
"""
import os

from brain.context import Context
from control.actions import Failed
from tests.test_close import EDIT, FF, FILES, Actions, Bridge, Shell, win
from tools import close, go_back, open_website


class BackShell(Shell):
    """Firefox tabs have a history; Files has folders; Text Editor has no back step."""

    def __init__(self):
        super().__init__()
        self.history = {11: ["Main Page", "Talk"], 21: []}   # pages before the current one
        self.files = ["Home", "Documents"]
        self.wins[3]["title"] = "Documents"

    def key(self, combo):
        self.log.append(("key", combo))
        w = self.focused()
        if combo != "alt+Left":
            return
        if w["app_id"] == FF:
            tabs, k = self.tabs[w["id"]], self.active[w["id"]]
            tid, title, url = tabs[k]
            if self.history.get(tid):
                tabs[k] = (tid, self.history[tid].pop(), url + "#back")
        elif w["app_id"] == FILES and len(self.files) > 1:
            self.files.pop()
            w["title"] = self.files[-1]


class BackBridge(Bridge):
    def call(self, cmd, **args):
        if cmd == "get_url":
            s = self.shell
            w = s.focused()["id"]
            tid, title, url = s.tabs[w][s.active[w]]
            return {"tab_id": tid, "url": url, "load_seq": 0}
        return super().call(cmd, **args)


def setup(focused, connected=True):
    shell = BackShell()
    shell.activate_window(focused)
    acts = Actions(shell)
    go_back.init(shell, acts, BackBridge(shell, connected))
    open_website.init(shell, acts, None, None)   # address bar estimate only
    return shell


def test_firefox_back_with_bridge():
    s = setup(1)
    assert go_back.go_back(Context()) == "Back to Talk.", s.log
    assert ("key", "alt+Left") in s.log
    assert go_back.go_back(Context()) == "Back to Main Page."
    assert go_back.go_back(Context()) == "There's no page to go back to."


def test_firefox_back_without_bridge():
    s = setup(1, connected=False)
    assert go_back.go_back(Context()) == "Back to Talk."
    s.activate_window(2)
    assert go_back.go_back(Context()) == "Nothing changed. There may be no page to go back to."


def test_files_back():
    s = setup(3)
    assert go_back.go_back(Context()) == "Back to Home."
    assert go_back.go_back(Context()) == "Nothing changed in Files. There may be nothing to go back to."


def test_app_without_back():
    s = setup(4)
    assert go_back.go_back(Context()) == "Nothing changed in Text Editor. There may be nothing to go back to."


def test_front_window_skips_kikus_terminal():
    term = dict(win(9, "kitty.desktop", "kiku", focused=True), pid=os.getppid())
    files = dict(win(3, FILES, "Home"), pid=1234)
    hidden = dict(win(5, EDIT, "x"), pid=1235, minimized=True)
    assert close.front_window([term, hidden, files])["id"] == 3
    assert close.front_window([dict(files, focused=True), term])["id"] == 3
    assert close.front_window([term]) is None


def test_back_from_kikus_terminal():
    s = setup(1)
    s.wins[9] = dict(win(9, "kitty.desktop", "kiku"), pid=os.getppid())
    s.activate_window(9)   # typing in --text mode: Kiku's terminal has focus
    assert go_back.go_back(Context()) == "Back to Talk.", s.log
    assert s.focused()["id"] == 1   # Firefox was brought up first, the terminal got no Alt+Left


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
    print(f"[KIKU] test: go_back {'all passed' if not fails else f'{fails} failed'}")
    sys.exit(fails != 0)
