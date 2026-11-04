// Kiku overlay: the face icon, highlights, scan line and caption.
// All actors are non-reactive (click-through) and live in one layer kept on top of uiGroup.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const ICON_SIZE = 64;       // logical px on screen (assets are 96px for HiDPI)
const GAP = 6;              // icon sits this far above-right of the target point
const MOVE_MIN_MS = 120;
const MOVE_MAX_MS = 350;
const MOVE_FULL_PX = 1500;  // distance (logical px) that gets MOVE_MAX_MS
const FADE_MS = 120;
const FOLLOW_TAU_MS = 45;   // follow smoothing: lower = tighter to the pointer
const ACCENT = 'rgba(176, 0, 254, 0.9)';

export const STATES = ['idle', 'listen', 'think', 'talk'];

// The ONE place that deals with monitor scaling.
// D-Bus coordinates are global stage coordinates, used as-is. With fractional scaling
// (scale-monitor-framebuffer) the stage is already in logical px and this returns 1;
// with integer scaling on a physical-px stage it returns 2, 3, ...
// Only our own design sizes (icon, gaps, line widths) go through px().
export function uiScale() {
    return St.ThemeContext.get_for_stage(global.stage).scale_factor;
}

function px(n) {
    return n * uiScale();
}

function monitorAt(x, y) {
    const lm = Main.layoutManager;
    return lm.monitors.find(m =>
        x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height) ?? lm.primaryMonitor;
}

// actor.ease() as a promise that resolves to true (finished) or false (stopped early).
function ease(actor, params) {
    return new Promise(resolve => {
        actor.ease({...params, onStopped: finished => resolve(finished)})?.catch?.(() => {});
    });
}

// Endless animation. Uses a raw transition so it does not block unredirect of fullscreen windows.
function loop(actor, prop, from, to, ms, mode, autoReverse) {
    const t = new Clutter.PropertyTransition({
        property_name: prop,
        interval: new Clutter.Interval({value_type: actor.find_property(prop).value_type}),
        progress_mode: mode,
        duration: ms,
        repeat_count: -1,
        auto_reverse: autoReverse,
    });
    t.set_from(from);
    t.set_to(to);
    actor.add_transition(`kiku-${prop}`, t);
}

function icon(path, name) {
    return new Gio.FileIcon({file: Gio.File.new_for_path(`${path}/assets/${name}.png`)});
}

export class Overlay {
    constructor(extPath) {
        this._idleIcon = icon(extPath, 'idle');
        this._actionIcon = icon(extPath, 'action');
        this._timers = new Map();   // GLib source id -> resolve
        this._transient = new Set();   // pulse rings and scans, destroyed on abort
        this._talkTimer = 0;
        this._state = 'idle';
        this._follow = null;   // Clutter.Timeline while following the pointer

        // Hidden until a client calls Show (see extension.js).
        this._layer = new Clutter.Actor({name: 'kiku-overlay', reactive: false, visible: false});
        this._kiku = new St.Widget({reactive: false});
        this._face = new St.Icon({gicon: this._idleIcon, icon_size: ICON_SIZE, reactive: false});
        this._face.set_pivot_point(0.5, 0.5);
        this._caption = new St.Label({
            reactive: false,
            visible: false,
            style: 'background-color: rgba(24, 24, 24, 0.88); color: #fff; ' +
                'border-radius: 6px; padding: 3px 8px; font-size: 10.5pt;',
        });
        this._highlight = new St.Widget({
            reactive: false,
            visible: false,
            style: `border: 2px solid ${ACCENT}; border-radius: 6px;`,
        });
        this._kiku.add_child(this._face);
        this._kiku.add_child(this._caption);
        this._layer.add_child(this._highlight);
        this._layer.add_child(this._kiku);

        const ui = Main.layoutManager.uiGroup;
        ui.add_child(this._layer);
        // Stay above app windows. A popup being added (child-added) isn't the only thing that changes
        // the stacking: windows restacking or the overview closing can put the window group above us,
        // which hid Kiku behind apps (visible only on the desktop / in the overview / after Alt+Tab).
        ui.connectObject('child-added', () => this._raise(), this);
        global.display.connectObject('restacked', () => this._raise(), this);
        Main.overview.connectObject('hidden', () => this._raise(), 'shown', () => this._raise(), this);
        this._raise();

        this._needHome = true;   // next show() starts from the home spot
        this._caption.set_position(0, px(ICON_SIZE + 4));
        this.setState('idle');
        console.log('[KIKU] overlay ready');
    }

    get shown() {
        return this._layer.visible && this._layer.opacity > 0;
    }

    // Start spot: bottom-right of the primary monitor. null if there is no monitor.
    _homePos() {
        const m = Main.layoutManager.primaryMonitor;
        if (!m)
            return null;
        return [Math.round(m.x + m.width - px(ICON_SIZE + 40)),
            Math.round(m.y + m.height - px(ICON_SIZE + 80))];
    }

    // Jump to home (used on show).
    _goHome() {
        this.followPointer(false);
        this._kiku.remove_all_transitions();
        this._target = null;
        const home = this._homePos();
        if (home)
            this._kiku.set_position(...home);
        this._needHome = false;
    }

    // Ease back to home after a task. Resolves true when there (or already there).
    goHome() {
        this.followPointer(false);
        this._target = null;
        const home = this._homePos();
        if (!home || (this._kiku.x === home[0] && this._kiku.y === home[1]))
            return Promise.resolve(true);
        this._raise();
        const d = Math.hypot(home[0] - this._kiku.x, home[1] - this._kiku.y) / uiScale();
        const ms = Math.round(MOVE_MIN_MS + (MOVE_MAX_MS - MOVE_MIN_MS) * Math.min(1, d / MOVE_FULL_PX));
        return ease(this._kiku, {x: home[0], y: home[1], duration: ms, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
    }

    show() {
        const l = this._layer;
        l.remove_all_transitions();
        if (!l.visible || this._needHome) {   // fresh start (also mid fade-out): from home
            this._goHome();
            this.showCaption('');
        }
        if (!l.visible) {
            l.opacity = 0;
            l.show();
        }
        this._raise();
        ease(l, {opacity: 255, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        console.log('[KIKU] overlay shown');
    }

    hide() {
        const l = this._layer;
        if (!l.visible)
            return;
        this.abort();
        this._needHome = true;
        l.remove_all_transitions();
        ease(l, {opacity: 0, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD})
            .then(done => done && l.hide());
        console.log('[KIKU] overlay hidden');
    }

    // Cheap when already on top; called before everything Kiku draws.
    _raise() {
        const parent = this._layer?.get_parent();
        if (parent && parent.last_child !== this._layer)
            parent.set_child_above_sibling(this._layer, null);
    }

    _iconPosFor(x, y) {
        const s = px(ICON_SIZE), g = px(GAP), m = monitorAt(x, y);
        let ix = x + g, iy = y - g - s;
        if (m && ix + s > m.x + m.width)
            ix = x - g - s;   // no room on the right: go left
        if (m && iy < m.y)
            iy = y + g;       // no room above: go below
        return [Math.round(ix), Math.round(iy)];
    }

    _sleep(ms) {
        return new Promise(resolve => {
            const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(0, ms), () => {
                this._timers.delete(id);
                resolve(true);
                return GLib.SOURCE_REMOVE;
            });
            this._timers.set(id, resolve);
        });
    }

    moveTo(x, y) {
        this._raise();
        this.followPointer(false);
        const [ix, iy] = this._iconPosFor(x, y);
        this._target = [x, y];
        const d = Math.hypot(ix - this._kiku.x, iy - this._kiku.y) / uiScale();
        const ms = Math.round(MOVE_MIN_MS + (MOVE_MAX_MS - MOVE_MIN_MS) * Math.min(1, d / MOVE_FULL_PX));
        return ease(this._kiku, {x: ix, y: iy, duration: ms, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
    }

    // Track the pointer every frame (synced to the display) with frame-rate independent smoothing.
    followPointer(on) {
        if (!on) {
            this._follow?.stop();
            this._follow = null;
            return;
        }
        if (this._follow)
            return;
        this._kiku.remove_all_transitions();
        let last = GLib.get_monotonic_time();
        let fx = this._kiku.x, fy = this._kiku.y;   // unrounded position
        const t = new Clutter.Timeline({actor: this._kiku, duration: 1000, repeat_count: -1});
        t.connect('new-frame', () => {
            const now = GLib.get_monotonic_time();
            const dt = (now - last) / 1000;
            last = now;
            const [px_, py_] = global.get_pointer();
            this._target = [px_, py_];
            const [tx, ty] = this._iconPosFor(px_, py_);
            const k = 1 - Math.exp(-dt / FOLLOW_TAU_MS);
            fx += (tx - fx) * k;
            fy += (ty - fy) * k;
            if (Math.abs(tx - fx) < 0.5 && Math.abs(ty - fy) < 0.5)
                [fx, fy] = [tx, ty];
            this._kiku.set_position(Math.round(fx), Math.round(fy));
        });
        t.start();
        this._follow = t;
    }

    async hover(x, y, ms) {
        if (!await this.moveTo(x, y))
            return false;
        return this._sleep(ms);
    }

    clickPulse() {
        this._raise();
        const [x, y] = this._target ?? [this._kiku.x, this._kiku.y + px(ICON_SIZE)];
        const size = px(18);
        const ring = new St.Widget({
            reactive: false,
            style: `border: 2px solid ${ACCENT}; border-radius: 999px;`,
            x: Math.round(x - size / 2),
            y: Math.round(y - size / 2),
            width: size,
            height: size,
            scale_x: 0.5,
            scale_y: 0.5,
        });
        ring.set_pivot_point(0.5, 0.5);
        this._addTransient(ring);
        ease(ring, {
            scale_x: 1.6, scale_y: 1.6, opacity: 0,
            duration: 260, mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        }).then(() => this._dropTransient(ring));
    }

    _addTransient(actor) {
        this._layer.insert_child_below(actor, this._kiku);
        this._transient.add(actor);
    }

    _dropTransient(actor) {
        if (this._transient.delete(actor))
            actor.destroy();
    }

    setState(s) {
        if (!STATES.includes(s))
            return false;
        this._raise();
        const f = this._face;
        f.remove_all_transitions();
        f.set({translation_y: 0, scale_x: 1, scale_y: 1, rotation_angle_z: 0});
        f.gicon = this._idleIcon;
        if (this._talkTimer) {
            GLib.source_remove(this._talkTimer);
            this._talkTimer = 0;
        }
        const sine = Clutter.AnimationMode.EASE_IN_OUT_SINE;
        if (s === 'idle') {
            loop(f, 'translation_y', 0, -px(2), 1600, sine, true);
        } else if (s === 'listen') {
            loop(f, 'scale_x', 1, 1.08, 500, sine, true);
            loop(f, 'scale_y', 1, 1.08, 500, sine, true);
        } else if (s === 'think') {
            loop(f, 'rotation_angle_z', 0, 360, 2400, Clutter.AnimationMode.LINEAR, false);
        } else if (s === 'talk') {
            f.gicon = this._actionIcon;
            this._talkTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 170, () => {
                f.gicon = f.gicon === this._idleIcon ? this._actionIcon : this._idleIcon;
                return GLib.SOURCE_CONTINUE;
            });
        }
        this._state = s;
        return true;
    }

    highlightRect(x, y, w, h) {
        this._raise();
        const hl = this._highlight;
        hl.set({x: Math.round(x), y: Math.round(y), width: Math.round(w), height: Math.round(h)});
        if (!hl.visible) {
            hl.opacity = 0;
            hl.show();
        }
        ease(hl, {opacity: 255, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    clearHighlight() {
        const hl = this._highlight;
        if (!hl.visible)
            return;
        ease(hl, {opacity: 0, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD})
            .then(done => done && hl.hide());
    }

    async scanRect(x, y, w, h, ms) {
        this._raise();
        const frame = new St.Widget({
            reactive: false,
            style: `border: 1px solid ${ACCENT}; border-radius: 4px;`,
            x: Math.round(x), y: Math.round(y), width: Math.round(w), height: Math.round(h),
        });
        const lineH = px(2);
        const line = new St.Widget({
            reactive: false,
            style: `background-color: ${ACCENT};`,
            x: Math.round(x), y: Math.round(y), width: Math.round(w), height: lineH,
        });
        const scan = new Clutter.Actor({reactive: false});
        scan.add_child(frame);
        scan.add_child(line);
        this._addTransient(scan);

        const done = await ease(line, {
            y: Math.round(y + h - lineH), duration: Math.max(0, ms), mode: Clutter.AnimationMode.LINEAR,
        });
        if (this._transient.has(scan)) {
            await ease(scan, {opacity: 0, duration: FADE_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            this._dropTransient(scan);
        }
        return done;
    }

    showCaption(text) {
        if (text)
            this._raise();
        this._caption.text = text;
        this._caption.visible = text !== '';
    }

    monitors() {
        const lm = Main.layoutManager;
        return lm.monitors.map(m => ({
            index: m.index,
            x: m.x,
            y: m.y,
            width: m.width,
            height: m.height,
            primary: m.index === lm.primaryIndex,
            scale: global.display.get_monitor_scale(m.index),
            ui_scale: uiScale(),
        }));
    }

    // Panic: stop everything now. Pending MoveTo/Hover/ScanRect calls return early.
    abort() {
        this.followPointer(false);
        this._kiku.remove_all_transitions();
        for (const [id, resolve] of this._timers) {
            GLib.source_remove(id);
            resolve(false);
        }
        this._timers.clear();
        for (const actor of [...this._transient])
            this._dropTransient(actor);
        this._highlight.remove_all_transitions();
        this._highlight.hide();
        this.showCaption('');
        this.setState('idle');
    }

    destroy() {
        this.abort();
        this._layer.remove_all_transitions();
        this._face.remove_all_transitions();
        if (this._talkTimer)
            GLib.source_remove(this._talkTimer);
        this._talkTimer = 0;
        Main.layoutManager.uiGroup.disconnectObject(this);
        global.display.disconnectObject(this);
        Main.overview.disconnectObject(this);
        this._layer.destroy();
        this._layer = this._kiku = this._face = this._caption = this._highlight = null;
    }
}
