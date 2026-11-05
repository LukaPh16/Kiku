// Kiku bridge, background page. Keeps a WebSocket to Kiku (ws://127.0.0.1:<port>), says hello
// with the token, then answers requests {id, cmd, args} with {id, ok, result | error}.
// Tab-level commands are handled here; page commands go to content.js in the active tab.
'use strict';

const DEFAULT_PORT = 8766;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 5000;
const PAGE_CMDS = new Set(['get_viewport', 'get_elements', 'get_paragraphs', 'get_blocks', 'line_ranges',
    'select_range', 'clear_selection', 'scroll_into_view', 'click', 'dom_text', 'get_rows', 'get_focus']);

let ws = null;
let status = 'starting';
let retryMs = RETRY_MIN_MS;
let retryTimer = null;
const loadSeq = new Map();   // tabId -> number of top-level loads started (for "wait for load")

function log(msg) {
    console.log(`[KIKU] bridge: ${msg}`);
}

// ---- connection ----

// Port + token: set by browser/setup_firefox.py through Firefox policy (storage.managed),
// else typed into the options page (storage.local).
async function settings() {
    const local = await browser.storage.local.get(['port', 'token']);
    const managed = await browser.storage.managed.get(['port', 'token']).catch(() => ({}));
    return {port: managed.port || local.port, token: managed.token || local.token, managed: !!managed.token};
}

async function connect() {
    clearTimeout(retryTimer);
    if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
    }
    const {port, token} = await settings();
    if (!token) {
        status = 'no token set (open the add-on options)';
        log(status);
        return;   // storage.onChanged reconnects once a token is saved
    }
    const sock = new WebSocket(`ws://127.0.0.1:${port || DEFAULT_PORT}`);
    ws = sock;
    status = 'connecting';
    sock.onopen = () => {
        sock.send(JSON.stringify({type: 'hello', token, version: 1, agent: navigator.userAgent}));
    };
    sock.onmessage = ev => onMessage(sock, ev.data);
    sock.onclose = ev => {
        if (ws !== sock)
            return;
        ws = null;
        status = ev.code === 4001 ? 'token rejected' : 'disconnected';
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    };
}

async function onMessage(sock, data) {
    let msg;
    try {
        msg = JSON.parse(data);
    } catch {
        return;
    }
    if (msg.type === 'welcome') {
        status = 'connected';
        retryMs = RETRY_MIN_MS;
        log('connected');
        return;
    }
    if (msg.id === undefined)
        return;
    let reply;
    try {
        reply = {id: msg.id, ok: true, result: await handle(msg.cmd, msg.args || {})};
    } catch (e) {
        reply = {id: msg.id, ok: false, error: String(e && e.message || e)};
    }
    if (sock.readyState === WebSocket.OPEN)
        sock.send(JSON.stringify(reply));
}

// ---- commands ----

// windowTitle: title of the Firefox window GNOME says is focused (sent by Kiku). With several
// windows, Firefox's own lastFocusedWindow can be stale on Wayland, so the title decides.
async function activeTab(windowTitle) {
    if (windowTitle) {
        const hits = (await browser.tabs.query({active: true}))
            .filter(t => t.title && windowTitle.startsWith(t.title))
            .sort((a, b) => b.title.length - a.title.length);
        if (hits.length)
            return hits[0];
    }
    const [tab] = await browser.tabs.query({active: true, lastFocusedWindow: true});
    if (!tab)
        throw new Error('no active tab');
    return tab;
}

function tabInfo(tab) {
    return {
        tab_id: tab.id, window_id: tab.windowId, url: tab.url || '', title: tab.title || '',
        status: tab.status || '', load_seq: loadSeq.get(tab.id) || 0,
    };
}

async function handle(cmd, args) {
    if (cmd === 'ping')
        return 'pong';
    const windowTitle = args._window_title;
    delete args._window_title;
    if (cmd === 'find_tabs') {   // tabs on a host, e.g. mail.google.com
        const tabs = await browser.tabs.query({url: `*://${args.host}/*`});
        return tabs.map(t => ({...tabInfo(t), active: t.active}));
    }
    if (cmd === 'activate_tab') {
        const t = await browser.tabs.update(args.tab_id, {active: true});
        return tabInfo(t);
    }
    const tab = await activeTab(windowTitle);
    if (cmd === 'get_url')
        return tabInfo(tab);
    if (cmd === 'navigate') {
        if (!args.url)
            throw new Error('navigate needs url');
        const t = await browser.tabs.update(tab.id, {url: args.url});
        return tabInfo(t);
    }
    if (cmd === 'go_back') {
        await browser.tabs.goBack(tab.id);
        return tabInfo(tab);
    }
    if (PAGE_CMDS.has(cmd))
        return pageCall(tab, cmd, args);
    throw new Error(`unknown command ${cmd}`);
}

async function pageCall(tab, cmd, args) {
    const msg = {kiku: cmd, args};
    try {
        return await browser.tabs.sendMessage(tab.id, msg, {frameId: 0});
    } catch (e) {
        // Tabs that were open before the add-on loaded have no content script yet.
        if (!/establish connection|Receiving end/i.test(String(e.message)))
            throw e;
        try {
            await browser.tabs.executeScript(tab.id, {file: 'content.js', frameId: 0});
        } catch {
            throw new Error(`can't read this page (${tab.url})`);
        }
        return browser.tabs.sendMessage(tab.id, msg, {frameId: 0});
    }
}

// ---- events ----

browser.tabs.onUpdated.addListener((tabId, info) => {
    if (info.status === 'loading')
        loadSeq.set(tabId, (loadSeq.get(tabId) || 0) + 1);
});
browser.tabs.onRemoved.addListener(tabId => loadSeq.delete(tabId));

browser.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.token || changes.port)) {
        retryMs = RETRY_MIN_MS;
        connect();
    }
});

browser.runtime.onMessage.addListener(msg => {
    if (msg && msg.kiku === 'status')
        return Promise.resolve(status);
    if (msg && msg.kiku === 'managed')
        return settings().then(s => s.managed);
    return undefined;
});

browser.runtime.onInstalled.addListener(async () => {
    const {token} = await settings();
    if (!token)
        browser.runtime.openOptionsPage();
});

connect();
