"""open_app name matching on a fixed app list (no kiku-shell needed).

  venv/bin/python -m tests.test_open_app
"""
from tools.open_app import resolve


def app(app_id, name, generic="", exec_="", keywords=(), running=False):
    return {"app_id": app_id, "name": name, "generic": generic, "exec": exec_,
            "keywords": list(keywords), "running": running}


APPS = [
    app("firefox_firefox.desktop", "Firefox", "Web Browser", "firefox"),
    app("spotify_spotify.desktop", "Spotify", "Music Player", "spotify"),
    app("org.gnome.Nautilus.desktop", "Files", "", "nautilus", ["folder", "manager", "explore"]),
    app("org.gnome.Ptyxis.desktop", "Terminal", "", "ptyxis", ["shell", "prompt", "command"]),
    app("kitty.desktop", "kitty", "Terminal emulator", "kitty"),
    app("com.microsoft.VSCode.desktop", "Visual Studio Code", "Text Editor", "code"),
    app("org.gnome.TextEditor.desktop", "Text Editor", "", "gnome-text-editor"),
    app("org.gnome.Calculator.desktop", "Calculator", "", "gnome-calculator"),
    app("org.gnome.Settings.desktop", "Settings", "", "gnome-control-center"),
    app("google-chrome.desktop", "Google Chrome", "Web Browser", "google-chrome-stable"),
    app("com.anthropic.Claude.desktop", "Claude", "", "claude-desktop"),
    app("a.desktop", "Notes", ""), app("b.desktop", "Notes", ""),
]

CASES = {
    "spotify": "spotify_spotify.desktop",
    "Firefox": "firefox_firefox.desktop",
    "files": "org.gnome.Nautilus.desktop",
    "terminal": "org.gnome.Ptyxis.desktop",
    "kitty": "kitty.desktop",
    "vs code": "com.microsoft.VSCode.desktop",
    "visual studio code": "com.microsoft.VSCode.desktop",
    "calculator": "org.gnome.Calculator.desktop",
    "text editor": "org.gnome.TextEditor.desktop",
    "the browser": None,            # "the" isn't stripped here (the router does it): ask, don't guess
    "browser": "firefox_firefox.desktop",
    "chrome": "google-chrome.desktop",
    "file manager": "org.gnome.Nautilus.desktop",
    "photoshop": None,
    "fil": None,
    "notes": None,                  # two apps named Notes -> ask which
}


def test_resolve():
    bad = []
    for spoken, want in CASES.items():
        got = resolve(spoken, APPS)
        got_id = None if isinstance(got, str) else got["app_id"]
        if got_id != want:
            bad.append((spoken, got, want))
        print(f"[KIKU] test: {'ok ' if got_id == want else 'BAD'} {spoken!r} -> "
              f"{got if isinstance(got, str) else got['name']}")
    assert not bad, bad


def test_tie_prefers_running():
    apps = APPS[:-2] + [app("a.desktop", "Notes", ""), app("b.desktop", "Notes", "", running=True)]
    assert resolve("notes", apps)["app_id"] == "b.desktop"


if __name__ == "__main__":
    test_resolve()
    test_tie_prefers_running()
    print("[KIKU] test: open_app all passed")
