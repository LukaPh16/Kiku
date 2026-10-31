#!/usr/bin/env bash
# Install the kiku-shell GNOME Shell extension for this user.
#   ./install.sh          symlink the extension (edits in the repo go live after re-login)
#   ./install.sh --copy   copy it instead
set -euo pipefail

UUID="kiku-shell@kiku"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SRC="$HERE/$UUID"
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
DEST="$DEST_DIR/$UUID"
SCHEMA="org.gnome.shell.extensions.kiku-shell"

log() { echo "[KIKU] $*"; }

# 1. Compile the settings schema (hotkeys).
glib-compile-schemas "$SRC/schemas"
log "compiled schemas"

# 2. Install.
mkdir -p "$DEST_DIR"
if [[ -L "$DEST" || -e "$DEST" ]]; then
    rm -rf "$DEST"
fi
if [[ "${1:-}" == "--copy" ]]; then
    cp -rL "$SRC" "$DEST"
    log "copied to $DEST"
else
    ln -s "$SRC" "$DEST"
    log "linked $DEST -> $SRC"
fi

# 3. Hotkeys from config.py (the source of truth).
read -r PANIC PTT < <(cd "$ROOT" && python3 -c 'import config; print(config.HOTKEY_PANIC, config.HOTKEY_PTT)')
gsettings --schemadir "$SRC/schemas" set "$SCHEMA" panic "['$PANIC']"
gsettings --schemadir "$SRC/schemas" set "$SCHEMA" ptt "['$PTT']"
log "hotkeys: panic=$PANIC ptt=$PTT"

# 4. Enable.
if gnome-extensions info "$UUID" >/dev/null 2>&1; then
    gnome-extensions enable "$UUID"
    log "enabled $UUID"
    log "the running shell still has the OLD code loaded."
    log ">>> Log out and back in to load this version. <<<"
else
    # The running shell scans the extensions folder only at login, so it doesn't know us yet.
    # Add us to enabled-extensions directly; the shell picks it up at next login.
    python3 - "$UUID" <<'EOF'
import ast, subprocess, sys
uuid = sys.argv[1]
cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     capture_output=True, text=True, check=True).stdout.strip()
exts = [] if cur.startswith("@as") else ast.literal_eval(cur)
if uuid not in exts:
    exts.append(uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(exts)], check=True)
EOF
    log "added $UUID to enabled-extensions"
    log ">>> New extension: log out and back in to load it. <<<"
fi
log "after login, check: gdbus introspect --session --dest org.kiku.Shell --object-path /org/kiku/Shell"
