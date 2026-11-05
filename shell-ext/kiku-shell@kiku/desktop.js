// Read-only desktop queries (windows, apps, dock icons) + window/app activation + screenshots.
// Rects are global stage px: {x, y, width, height}.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

Gio._promisify(Shell.Screenshot.prototype, 'screenshot');

const WINDOW_TYPES = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG];

function rect(r) {
    return {x: r.x, y: r.y, width: r.width, height: r.height};
}

function actorRect(actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return {x: Math.round(x), y: Math.round(y), width: Math.round(w), height: Math.round(h)};
}

function onSomeMonitor(r) {
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    return Main.layoutManager.monitors.some(m =>
        cx >= m.x && cx < m.x + m.width && cy >= m.y && cy < m.y + m.height);
}

function appForWindow(w) {
    return Shell.WindowTracker.get_default().get_window_app(w);
}

function userWindows() {
    // Top of the stack first.
    return global.get_window_actors().map(a => a.get_meta_window())
        .filter(w => w && WINDOW_TYPES.includes(w.get_window_type()) && !w.is_skip_taskbar())
        .reverse();
}

// GJS gives D-Bus 't' (uint64) as a Number; window ids fit in 2^53.
function findWindow(id) {
    return userWindows().find(w => Number(w.get_id()) === Number(id)) ?? null;
}

export function listWindows() {
    const focus = global.display.focus_window;
    const ws = global.workspace_manager.get_active_workspace();
    return userWindows().map(w => ({
        id: Number(w.get_id()),
        app_id: appForWindow(w)?.get_id() ?? w.get_wm_class() ?? '',
        title: w.get_title() ?? '',
        frame: rect(w.get_frame_rect()),
        buffer: rect(w.get_buffer_rect()),   // includes client-side shadows
        focused: w === focus,
        minimized: w.minimized,
        monitor: w.get_monitor(),
        on_active_workspace: w.located_on_workspace(ws),
        pid: w.get_pid(),
    }));
}

export function listApps() {
    const sys = Shell.AppSystem.get_default();
    const running = new Set(sys.get_running().map(a => a.get_id()));
    return sys.get_installed().filter(info => info.should_show()).map(info => ({
        app_id: info.get_id(),
        name: info.get_name(),
        generic: info.get_generic_name?.() ?? '',
        keywords: info.get_keywords?.() ?? [],
        exec: (info.get_executable() ?? '').split('/').pop(),
        running: running.has(info.get_id()),
    }));
}

function findActors(root, pred, out = []) {
    for (const c of root.get_children()) {
        if (pred(c))
            out.push(c);
        else
            findActors(c, pred, out);
    }
    return out;
}

// App icons of Ubuntu Dock / Dash to Dock (container "dashtodockContainer"), else the overview dash.
// visible=false when the dock is hidden (autohide) or the icon is off-screen.
export function dashIcons() {
    let docks = findActors(Main.layoutManager.uiGroup, a => a.name === 'dashtodockContainer');
    if (!docks.length && Main.overview.dash)
        docks = [Main.overview.dash];
    const out = [];
    for (const dock of docks) {
        for (const icon of findActors(dock, a => a.app instanceof Shell.App)) {
            const r = actorRect(icon);
            out.push({
                app_id: icon.app.get_id(),
                name: icon.app.get_name(),
                rect: r,
                running: icon.app.get_state() !== Shell.AppState.STOPPED,
                visible: icon.is_mapped() && dock.is_mapped() && r.width > 0 && onSomeMonitor(r),
            });
        }
    }
    return out;
}

// Activities overview: is it open, where is the search entry, does it have keyboard focus.
export function overviewState() {
    const ov = Main.overview;
    const entry = ov.searchEntry;
    const focus = global.stage.get_key_focus();
    return {
        visible: ov.visible && !ov.animationInProgress,
        animating: ov.animationInProgress,
        search_rect: entry?.is_mapped() ? actorRect(entry) : null,
        search_focused: !!entry && !!focus && (focus === entry || entry.contains(focus)),
        search_text: entry?.get_text() ?? '',
    };
}

export function activateWindow(id) {
    const w = findWindow(id);
    if (!w)
        return false;
    const t = global.get_current_time();
    const ws = w.get_workspace();
    if (ws && ws !== global.workspace_manager.get_active_workspace())
        ws.activate_with_focus(w, t);
    else
        w.activate(t);
    return true;
}

// Asks the window to close, like its close button (the app may still ask to save first).
export function closeWindow(id) {
    const w = findWindow(id);
    if (!w || !w.can_close())
        return false;
    w.delete(global.get_current_time());
    return true;
}

export function launchApp(appId) {
    const app = Shell.AppSystem.get_default().lookup_app(appId);
    if (!app)
        return false;
    if (app.get_state() === Shell.AppState.STOPPED)
        app.activate();
    else
        app.open_new_window(-1);
    return true;
}

// Full-stage PNG in /tmp, no portal and no flash. Image px may differ from stage px (scaling).
export async function screenshot() {
    const path = GLib.build_filenamev([GLib.get_tmp_dir(), `kiku-shot-${GLib.get_monotonic_time()}.png`]);
    const file = Gio.File.new_for_path(path);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    try {
        await new Shell.Screenshot().screenshot(false, stream);
    } finally {
        stream.close(null);
    }
    return path;
}
