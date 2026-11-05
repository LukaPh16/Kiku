// kiku-shell: exports org.kiku.Shell on the session bus and owns the Kiku overlay + hotkeys.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import * as Desktop from './desktop.js';
import {Input} from './input.js';
import {Overlay, STATES} from './overlay.js';

const BUS_NAME = 'org.kiku.Shell';
const OBJECT_PATH = '/org/kiku/Shell';
const ERROR = 'org.kiku.Shell.Error';

// All coordinates are global stage (logical) pixels.
const IFACE = `
<node>
  <interface name="org.kiku.Shell">
    <method name="Show"/>
    <method name="Hide"/>
    <method name="MoveTo">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
    </method>
    <method name="GoHome"/>
    <method name="Hover">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
      <arg type="u" name="ms" direction="in"/>
    </method>
    <method name="FollowPointer">
      <arg type="b" name="on" direction="in"/>
    </method>
    <method name="ClickPulse"/>
    <method name="SetState">
      <arg type="s" name="state" direction="in"/>
    </method>
    <method name="HighlightRect">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
      <arg type="d" name="w" direction="in"/>
      <arg type="d" name="h" direction="in"/>
    </method>
    <method name="ClearHighlight"/>
    <method name="ScanRect">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
      <arg type="d" name="w" direction="in"/>
      <arg type="d" name="h" direction="in"/>
      <arg type="u" name="ms" direction="in"/>
    </method>
    <method name="ShowCaption">
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="Configure">
      <arg type="s" name="json" direction="in"/>
    </method>
    <method name="GetPointer">
      <arg type="d" name="x" direction="out"/>
      <arg type="d" name="y" direction="out"/>
    </method>
    <method name="GetMonitors">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="ListWindows">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="ListApps">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="DashIcons">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="ActivateWindow">
      <arg type="t" name="id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="CloseWindow">
      <arg type="t" name="id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="LaunchApp">
      <arg type="s" name="app_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="PointerMove">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
    </method>
    <method name="Click">
      <arg type="u" name="button" direction="in"/>
      <arg type="b" name="done" direction="out"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="dx" direction="in"/>
      <arg type="i" name="dy" direction="in"/>
      <arg type="b" name="done" direction="out"/>
    </method>
    <method name="KeyCombo">
      <arg type="s" name="combo" direction="in"/>
      <arg type="b" name="done" direction="out"/>
    </method>
    <method name="TypeText">
      <arg type="s" name="text" direction="in"/>
      <arg type="u" name="cps" direction="in"/>
      <arg type="i" name="skipped" direction="out"/>
    </method>
    <method name="OverviewState">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="Screenshot">
      <arg type="s" name="path" direction="out"/>
    </method>
    <method name="Abort"/>
    <signal name="Hotkey">
      <arg type="s" name="name"/>
    </signal>
  </interface>
</node>`;

// [settings key, allowed action modes]. Panic works everywhere; ptt only in a normal session.
const HOTKEYS = [
    ['panic', Shell.ActionMode.ALL],
    ['ptt', Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP],
];

// Slow methods (MoveTo, Hover, ScanRect, input) reply when done or aborted.
// With a signature, the promise's result is returned as the single out arg.
function reply(invocation, promise, signature = null) {
    promise.then(
        v => invocation.return_value(signature ? new GLib.Variant(`(${signature})`, [v]) : null),
        e => invocation.return_dbus_error(`${ERROR}.Failed`, String(e)));
}

class Service {
    constructor(overlay, input, onAbort) {
        this._o = overlay;
        this._in = input;
        this._onAbort = onAbort;
        this._watchId = 0;   // watches the client that called Show
    }

    // Show Kiku. It hides again on Hide or when the calling process leaves the bus (exit/crash).
    ShowAsync(_params, inv) {
        this._unwatch();
        const sender = inv.get_sender();
        this._watchId = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender,
            Gio.BusNameWatcherFlags.NONE, null, () => {
                console.log(`[KIKU] client ${sender} left; hiding`);
                this._unwatch();
                this._o.hide();
            });
        this._o.show();
        inv.return_value(null);
    }

    Hide() {
        this._unwatch();
        this._o.hide();
    }

    _unwatch() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
    }

    destroy() {
        this._unwatch();
    }

    MoveToAsync([x, y], inv) {
        reply(inv, this._o.moveTo(x, y));
    }

    GoHomeAsync(_params, inv) {
        reply(inv, this._o.goHome());
    }

    HoverAsync([x, y, ms], inv) {
        reply(inv, this._o.hover(x, y, ms));
    }

    FollowPointer(on) {
        this._o.followPointer(on);
    }

    ClickPulse() {
        this._o.clickPulse();
    }

    SetStateAsync([state], inv) {
        if (this._o.setState(state))
            inv.return_value(null);
        else
            inv.return_dbus_error(`${ERROR}.InvalidArgs`, `state must be one of ${STATES.join(', ')}`);
    }

    HighlightRect(x, y, w, h) {
        this._o.highlightRect(x, y, w, h);
    }

    ClearHighlight() {
        this._o.clearHighlight();
    }

    ScanRectAsync([x, y, w, h, ms], inv) {
        reply(inv, this._o.scanRect(x, y, w, h, ms));
    }

    ShowCaption(text) {
        this._o.showCaption(text);
    }

    // Visual settings from the client's config.py: {"icon_size": 64, "move_min_ms": 120, "move_max_ms": 350}
    ConfigureAsync([json], inv) {
        let cfg;
        try {
            cfg = JSON.parse(json);
        } catch {
            cfg = null;
        }
        if (!cfg || typeof cfg !== 'object') {
            inv.return_dbus_error(`${ERROR}.InvalidArgs`, 'Configure wants a JSON object');
            return;
        }
        this._o.configure(cfg);
        inv.return_value(null);
    }

    GetPointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    GetMonitors() {
        return JSON.stringify(this._o.monitors());
    }

    // ---- desktop ----

    ListWindows() {
        return JSON.stringify(Desktop.listWindows());
    }

    ListApps() {
        return JSON.stringify(Desktop.listApps());
    }

    DashIcons() {
        return JSON.stringify(Desktop.dashIcons());
    }

    OverviewState() {
        return JSON.stringify(Desktop.overviewState());
    }

    ActivateWindow(id) {
        return Desktop.activateWindow(id);
    }

    CloseWindow(id) {
        return Desktop.closeWindow(id);
    }

    LaunchApp(appId) {
        return Desktop.launchApp(appId);
    }

    ScreenshotAsync(_params, inv) {
        reply(inv, Desktop.screenshot(), 's');
    }

    // ---- input (virtual devices) ----

    PointerMove(x, y) {
        this._in.pointerMove(x, y);
    }

    ClickAsync([button], inv) {
        reply(inv, this._in.click(button), 'b');
    }

    ScrollAsync([dx, dy], inv) {
        reply(inv, this._in.scroll(dx, dy), 'b');
    }

    KeyComboAsync([combo], inv) {
        reply(inv, this._in.keyCombo(combo), 'b');
    }

    TypeTextAsync([text, cps], inv) {
        reply(inv, this._in.typeText(text, cps), 'i');
    }

    // Same as the panic hotkey, for clients ("kiku stop").
    Abort() {
        this._onAbort();
    }
}

export default class KikuShellExtension extends Extension {
    enable() {
        this._overlay = new Overlay(this.path);
        this._input = new Input();

        this._service = new Service(this._overlay, this._input, () => this._abort());
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this._service);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._ownerId = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            null,
            () => console.log(`[KIKU] owns ${BUS_NAME}`),
            () => console.warn(`[KIKU] could not own ${BUS_NAME} (another instance?)`));

        this._settings = this.getSettings();
        for (const [name, modes] of HOTKEYS) {
            Main.wm.addKeybinding(name, this._settings, Meta.KeyBindingFlags.IGNORE_AUTOREPEAT, modes,
                () => this._onHotkey(name));
        }
        console.log('[KIKU] kiku-shell enabled');
    }

    _onHotkey(name) {
        console.log(`[KIKU] hotkey ${name}`);
        if (name === 'panic')
            this._abort();
        this._dbus.emit_signal('Hotkey', new GLib.Variant('(s)', [name]));
    }

    _abort() {
        this._overlay.abort();
        this._input.abort();
    }

    disable() {
        for (const [name] of HOTKEYS)
            Main.wm.removeKeybinding(name);
        this._settings = null;

        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
        this._service.destroy();
        this._service = null;
        this._overlay.destroy();   // returns any pending D-Bus calls
        this._overlay = null;
        this._input.destroy();
        this._input = null;
        this._dbus.unexport();
        this._dbus = null;
        console.log('[KIKU] kiku-shell disabled');
    }
}
