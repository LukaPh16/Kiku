// Virtual pointer + keyboard (Clutter virtual devices). Coordinates are global stage px.
// Every timed sequence can be aborted (panic); abort() also releases anything held down.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

const KEY_GAP_MS = 12;     // between press/release events of a combo
const CLICK_HOLD_MS = 40;

// Spoken/written key names -> Clutter keysym names
const NAMES = {
    ctrl: 'Control_L', control: 'Control_L', alt: 'Alt_L', shift: 'Shift_L',
    super: 'Super_L', meta: 'Super_L', win: 'Super_L', cmd: 'Super_L',
    enter: 'Return', return: 'Return', esc: 'Escape', escape: 'Escape', tab: 'Tab',
    space: 'space', backspace: 'BackSpace', delete: 'Delete', del: 'Delete', insert: 'Insert',
    up: 'Up', down: 'Down', left: 'Left', right: 'Right', home: 'Home', end: 'End',
    pageup: 'Page_Up', pagedown: 'Page_Down', menu: 'Menu', print: 'Print',
    plus: 'plus', minus: 'minus', comma: 'comma', period: 'period', slash: 'slash',
};

function now() {
    return GLib.get_monotonic_time();   // microseconds, what notify_* expect
}

export function keyvalFor(name) {
    const n = name.trim();
    const lower = n.toLowerCase();
    if (NAMES[lower])
        return Clutter[`KEY_${NAMES[lower]}`];
    if (/^f([1-9]|1[0-2])$/.test(lower))
        return Clutter[`KEY_${lower.toUpperCase()}`];
    if ([...n].length === 1)
        return Clutter.unicode_to_keysym(lower.codePointAt(0));
    return Clutter[`KEY_${n}`] ?? 0;   // raw keysym name, e.g. "KP_Enter"
}

function charKeyval(ch) {
    if (ch === '\n')
        return Clutter.KEY_Return;
    if (ch === '\t')
        return Clutter.KEY_Tab;
    return Clutter.unicode_to_keysym(ch.codePointAt(0));
}

export class Input {
    constructor() {
        const seat = Clutter.get_default_backend().get_default_seat();
        this._pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._timers = new Map();      // GLib source id -> resolve
        this._heldKeys = new Set();
        this._heldButtons = new Set();
    }

    _sleep(ms) {
        return new Promise(resolve => {
            const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(0, Math.round(ms)), () => {
                this._timers.delete(id);
                resolve(true);
                return GLib.SOURCE_REMOVE;
            });
            this._timers.set(id, resolve);
        });
    }

    _key(keyval, down) {
        this._keyboard.notify_keyval(now(), keyval,
            down ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
        if (down)
            this._heldKeys.add(keyval);
        else
            this._heldKeys.delete(keyval);
    }

    _button(button, down) {
        this._pointer.notify_button(now(), button,
            down ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
        if (down)
            this._heldButtons.add(button);
        else
            this._heldButtons.delete(button);
    }

    pointerMove(x, y) {
        this._pointer.notify_absolute_motion(now(), x, y);
    }

    // Resolves true when done, false if aborted.
    async click(button) {
        this._button(button, true);
        const ok = await this._sleep(CLICK_HOLD_MS);
        this._button(button, false);   // always release, even when aborted
        return ok;
    }

    // dx/dy in wheel steps: +dy scrolls down, +dx scrolls right.
    async scroll(dx, dy) {
        const steps = [];
        for (let i = 0; i < Math.abs(dy); i++)
            steps.push(dy > 0 ? Clutter.ScrollDirection.DOWN : Clutter.ScrollDirection.UP);
        for (let i = 0; i < Math.abs(dx); i++)
            steps.push(dx > 0 ? Clutter.ScrollDirection.RIGHT : Clutter.ScrollDirection.LEFT);
        for (const dir of steps) {
            this._pointer.notify_discrete_scroll(now(), dir, Clutter.ScrollSource.WHEEL);
            if (!await this._sleep(30))
                return false;
        }
        return true;
    }

    // "ctrl+l", "ctrl+shift+t", "super", "enter". Throws on an unknown key name.
    async keyCombo(combo) {
        const keyvals = combo.split('+').filter(s => s.trim()).map(name => {
            const kv = keyvalFor(name);
            if (!kv)
                throw new Error(`unknown key "${name}"`);
            return kv;
        });
        if (!keyvals.length)
            throw new Error('empty key combo');
        let ok = true;
        for (const kv of keyvals) {
            this._key(kv, true);
            if (!(ok = await this._sleep(KEY_GAP_MS)))
                break;
        }
        for (const kv of [...keyvals].reverse()) {
            if (this._heldKeys.has(kv)) {
                this._key(kv, false);
                if (ok)
                    ok = await this._sleep(KEY_GAP_MS);
            }
        }
        return ok;
    }

    // Types text at cps characters per second. Returns the number of characters it couldn't type.
    async typeText(text, cps) {
        const gap = 1000 / Math.max(1, Math.min(cps, 200));
        let skipped = 0;
        for (const ch of text) {
            const kv = charKeyval(ch);
            if (!kv) {
                skipped++;
                continue;
            }
            this._key(kv, true);
            this._key(kv, false);
            if (!await this._sleep(gap))
                return -1;
        }
        return skipped;
    }

    // Panic: stop pending sequences and release everything held.
    abort() {
        for (const [id, resolve] of this._timers) {
            GLib.source_remove(id);
            resolve(false);
        }
        this._timers.clear();
        for (const kv of [...this._heldKeys])
            this._key(kv, false);
        for (const b of [...this._heldButtons])
            this._button(b, false);
    }

    destroy() {
        this.abort();
        this._pointer = this._keyboard = null;
    }
}
