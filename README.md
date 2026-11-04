# Kiku

Desktop assistant for Ubuntu 26.04, GNOME on Wayland. Python (brain) talks over D-Bus to a GNOME Shell extension (`kiku-shell@kiku`) that draws the overlay and drives input.

## Setup

### System packages

```sh
sudo apt install python3-venv python3-dev \
    gir1.2-atspi-2.0 at-spi2-core python3-gi \
    libportaudio2 ffmpeg \
    mutter-dev-bin            # provides mutter-devkit, needed by `gnome-shell --devkit`
# optional: sudo apt install python3-dbus-next   (we use pip dbus-next in the venv instead)
```

### Python venv

```sh
python3 -m venv venv
. venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### AT-SPI in the venv

`control/locate.py` uses AT-SPI through the system PyGObject (`python3-gi`, `gir1.2-atspi-2.0`). The venv doesn't see system packages, so link it in once (same Python version as the system):

```sh
ln -s /usr/lib/python3/dist-packages/gi venv/lib/python3.14/site-packages/gi
```

GTK/GNOME apps always expose accessibility. Firefox and Chrome/Electron apps only do when assistive technology is on:

```sh
gsettings set org.gnome.desktop.interface toolkit-accessibility true   # then restart those apps
```

### Ollama model

Ollama is already installed (`/usr/local/bin/ollama`). If not: `curl -fsSL https://ollama.com/install.sh | sh`.

```sh
ollama pull qwen3:1.7b
```

### TTS voice

TTS is Kyutai Pocket TTS with the built-in `eve` voice (`TTS_VOICE` in `config.py`). `requirements.txt` pulls the CPU-only PyTorch build. The model downloads itself from Hugging Face on first use.

The faster-whisper `base.en` model downloads itself on first use (~150 MB).

## Run

```sh
./kiku.py        # Kiku appears; Ctrl+C to quit and it disappears
```

The extension loads at login but keeps Kiku hidden until `./kiku.py` (or any client) calls `Show`. It hides again on `Hide` or when that process exits.

## Desktop control

```sh
./main.py --text         # try: "open files", "open calculator", "switch to firefox"
venv/bin/python -m control.locate_debug "Firefox"            # highlight what was found
venv/bin/python -m control.locate_debug "Save" --source atspi
venv/bin/python -m control.locate_debug --list               # windows, dock icons, overview state
venv/bin/python -m tests.test_open_app                       # app name matching
venv/bin/python -m tests.test_browser                        # site names, geometry, bridge (no Firefox needed)
```

After changing `shell-ext/`, log out and back in (Wayland can't reload the shell in place).

### Headless test shell

You can test the extension without logging out: run a headless GNOME Shell on a private session bus,
with its own config dirs so it doesn't touch your settings:

```sh
T=$(mktemp -d); mkdir -p $T/data/gnome-shell/extensions $T/run; chmod 700 $T/run
ln -s "$PWD/shell-ext/kiku-shell@kiku" $T/data/gnome-shell/extensions/
dbus-run-session -- env XDG_CONFIG_HOME=$T/config XDG_DATA_HOME=$T/data XDG_RUNTIME_DIR=$T/run \
  GSETTINGS_BACKEND=keyfile bash -c '
    gsettings set org.gnome.shell enabled-extensions "[\"kiku-shell@kiku\", \"ubuntu-dock@ubuntu.com\"]"
    gnome-shell --headless --virtual-monitor 1920x1080 --no-x11 --wayland-display kiku-test &> $0/shell.log &
    sleep 5; export WAYLAND_DISPLAY=kiku-test
    printf "open files\nopen calculator\n" | ./main.py --text --mute
    kill %1' $T
```

Snap apps (Spotify, Firefox) don't start in it (no systemd); GNOME apps do.

## Firefox bridge

`browser/extension/` is a Firefox WebExtension (Manifest V2, persistent background page) that connects
to Kiku's WebSocket at `ws://127.0.0.1:8766` (`BROWSER_PORT` in `config.py`) and authenticates with a
token. Kiku uses it to read the current tab (URL, load state, elements with their rects, paragraphs),
select text, scroll, and click. The bridge only listens on localhost and only accepts
`moz-extension://` origins with the right token.

The token is generated on first run and kept in `secrets/browser_token`. To print it:

```sh
venv/bin/python -m browser.bridge --token     # port + token
```

### Load the extension for development (temporary)

1. Open `about:debugging#/runtime/this-firefox`, then **Load Temporary Add-on...**, and pick `browser/extension/manifest.json`.
2. The options page opens (or go to `about:addons`, select Kiku Bridge, then Preferences). Paste the port and token, then click Save.
3. Check it: `venv/bin/python -m browser.bridge` prints the current tab once the extension connects.

Temporary add-ons are removed when Firefox restarts. After editing the extension, click **Reload** on the
`about:debugging` page.

### Load it permanently

Release Firefox (including the Ubuntu snap) only installs **signed** extensions. Your options:

- **Self-distributed signing on AMO (recommended).** This is free, automatic and unlisted (it isn't published). Create API keys at
  <https://addons.mozilla.org/developers/addon/api/key/>, then:
  ```sh
  npx web-ext sign --source-dir browser/extension --channel unlisted \
      --api-key "$AMO_JWT_ISSUER" --api-secret "$AMO_JWT_SECRET"
  ```
  Install the `.xpi` it writes to `web-ext-artifacts/` via `about:addons`, then the gear menu, then **Install Add-on From File...**
  The add-on id (`kiku-bridge@kiku` in `manifest.json`) stays the same, so the saved token survives updates.
  Bump `version` in `manifest.json` before each new signing.
- **Firefox Developer Edition, Nightly or an unbranded build.** Set `xpinstall.signatures.required` to `false` in
  `about:config`, zip the folder (`cd browser/extension && zip -r ../kiku-bridge.xpi .`) and install it from file.
  This setting is ignored by release Firefox.

### Check the coordinates

Page rects are viewport CSS px. Kiku converts them to global screen px using the Firefox window frame
from kiku-shell, the viewport size from the extension, and `devicePixelRatio / monitor scale`
(see `browser/geometry.py`). It assumes the toolbars are on top: an open sidebar, the find bar or docked
devtools will shift the result. To see it on screen (stop `main.py` first, because both use the port):

```sh
venv/bin/python -m browser.debug                      # outlines the viewport, then the visible paragraphs
venv/bin/python -m browser.debug --text "Sign in"     # elements by visible text
venv/bin/python -m browser.debug --role link --limit 3
venv/bin/python -m browser.debug --select 0 0 40      # selects text in paragraph 0 and outlines it
```

Each outline should sit exactly on the element. Try it on both monitors and at a page zoom other than 100%.

### open_website

`./main.py --text`, then try "open wikipedia", "go to reddit.com", or "open the weather site" (this one searches).
Kiku focuses Firefox (opening it if needed), moves to the address bar, presses Ctrl+L, types the site visibly,
presses Enter and waits for the page to load through the bridge. Without the extension it still types the
site, but it can't tell when the page has loaded. Spoken names map to URLs through `KNOWN_SITES` in `config.py`.

## Developing the GNOME Shell extension (Wayland)

On Wayland the running shell cannot be restarted in place (no Alt+F2 `r`). Use a nested test shell for development.

Install it as a symlink so edits go live:

```sh
ln -s "$PWD/shell-ext" ~/.local/share/gnome-shell/extensions/kiku-shell@kiku
```

### Nested test shell (GNOME 49+, this machine runs GNOME Shell 50.1)

`--nested` was removed in GNOME 49; use `--devkit` (needs `mutter-dev-bin`):

```sh
MUTTER_DEBUG_DUMMY_MODE_SPECS=1920x1080 \
  dbus-run-session gnome-shell --devkit
```

- It runs in a window with its own session bus. Run `gnome-extensions enable kiku-shell@kiku` inside it (e.g. from a terminal opened in the nested shell).
- Logs from the extension go to this terminal's stdout.
- To reload after a code change: close the nested shell and start it again.
- To test fractional scaling: `MUTTER_DEBUG_DUMMY_MODE_SPECS=2560x1600@60` and set the scale in the nested shell's Settings > Displays.

### Real session

- New extensions and code changes are only picked up by the real shell after **logging out and back in**.
- Enable: `gnome-extensions enable kiku-shell@kiku`
- Logs: `journalctl -f -o cat /usr/bin/gnome-shell`
- Check the D-Bus API: `gdbus introspect --session --dest org.kiku.Shell --object-path /org/kiku/Shell`
