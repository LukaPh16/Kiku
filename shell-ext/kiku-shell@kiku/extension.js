// kiku-shell: exports org.kiku.Shell on the session bus and owns the Kiku overlay + hotkeys.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {Overlay, STATES} from './overlay.js';

const BUS_NAME = 'org.kiku.Shell';
const OBJECT_PATH = '/org/kiku/Shell';
const ERROR = 'org.kiku.Shell.Error';

// All coordinates are global stage (logical) pixels.
const IFACE = `
<node>
  <interface name="org.kiku.Shell">
    <method name="MoveTo">
      <arg type="d" name="x" direction="in"/>
      <arg type="d" name="y" direction="in"/>
    </method>
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
    <method name="GetPointer">
      <arg type="d" name="x" direction="out"/>
      <arg type="d" name="y" direction="out"/>
    </method>
    <method name="GetMonitors">
      <arg type="s" name="json" direction="out"/>
    </method>
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

// Slow methods (MoveTo, Hover, ScanRect) reply when the animation is done or aborted.
function reply(invocation, promise) {
    promise.then(
        () => invocation.return_value(null),
        e => invocation.return_dbus_error(`${ERROR}.Failed`, String(e)));
}

class Service {
    constructor(overlay) {
        this._o = overlay;
    }

    MoveToAsync([x, y], inv) {
        reply(inv, this._o.moveTo(x, y));
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

    GetPointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    GetMonitors() {
        return JSON.stringify(this._o.monitors());
    }
}

export default class KikuShellExtension extends Extension {
    enable() {
        this._overlay = new Overlay(this.path);

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, new Service(this._overlay));
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
            this._overlay.abort();
        this._dbus.emit_signal('Hotkey', new GLib.Variant('(s)', [name]));
    }

    disable() {
        for (const [name] of HOTKEYS)
            Main.wm.removeKeybinding(name);
        this._settings = null;

        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
        this._overlay.destroy();   // returns any pending D-Bus calls
        this._overlay = null;
        this._dbus.unexport();
        this._dbus = null;
        console.log('[KIKU] kiku-shell disabled');
    }
}
