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

### Ollama model

Ollama is already installed (`/usr/local/bin/ollama`). If not: `curl -fsSL https://ollama.com/install.sh | sh`.

```sh
ollama pull qwen3:1.7b
```

### Piper voice

```sh
mkdir -p models/piper && cd models/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
cd -
```

The faster-whisper `base.en` model downloads itself on first use (~150 MB).

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
