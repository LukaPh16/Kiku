// Kiku bridge, content script (top frame). Finds elements and paragraphs, selects text, scrolls,
// clicks. Elements get ids ("k12") valid for the life of the page. Rects are viewport CSS px;
// Python turns them into global screen coords using `view` (sent with every rect).
//
// Text: every text this script returns is the element's text nodes joined with whitespace runs
// collapsed to one space (script/style skipped). select_range offsets index into that same text.
'use strict';

if (!window.__kikuBridge) {
    window.__kikuBridge = true;

    const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    const INTERACTIVE = 'a[href], button, input, textarea, select, summary, label, [role=button], ' +
        '[role=link], [role=menuitem], [role=tab], [role=checkbox], [role=option], [contenteditable=""], ' +
        '[contenteditable=true]';
    const ROLES = {
        link: 'a[href], [role=link]',
        button: 'button, input[type=button], input[type=submit], input[type=reset], [role=button]',
        textbox: 'input:not([type]), input[type=text], input[type=search], input[type=email], ' +
            'input[type=url], input[type=password], input[type=tel], input[type=number], textarea, ' +
            '[contenteditable=""], [contenteditable=true], [role=textbox], [role=searchbox], [role=combobox]',
        searchbox: 'input[type=search], [role=searchbox]',
        heading: 'h1, h2, h3, h4, h5, h6, [role=heading]',
        paragraph: 'p',
        image: 'img, svg[role=img], [role=img]',
        checkbox: 'input[type=checkbox], [role=checkbox]',
        listitem: 'li, [role=listitem]',
        tab: '[role=tab]',
        menuitem: '[role=menuitem]',
    };

    const ids = new WeakMap();   // element -> id
    const els = new Map();       // id -> WeakRef(element)
    let nextId = 1;

    // ---- helpers ----

    function view() {
        return {
            inner_width: window.innerWidth, inner_height: window.innerHeight,
            outer_width: window.outerWidth, outer_height: window.outerHeight,
            dpr: window.devicePixelRatio, screen_x: window.screenX, screen_y: window.screenY,
            scroll_x: window.scrollX, scroll_y: window.scrollY, url: location.href,
        };
    }

    function idOf(el) {
        let id = ids.get(el);
        if (!id) {
            id = `k${nextId++}`;
            ids.set(el, id);
            els.set(id, new WeakRef(el));
        }
        return id;
    }

    function byId(id) {
        const el = els.get(id)?.deref();
        if (!el || !el.isConnected)
            throw new Error(`element ${id} is gone (page changed?)`);
        return el;
    }

    function r1(n) {
        return Math.round(n * 10) / 10;
    }

    function rectOf(r) {
        return {x: r1(r.x), y: r1(r.y), width: r1(r.width), height: r1(r.height)};
    }

    function rectsOf(target) {
        return Array.from(target.getClientRects(), rectOf).filter(r => r.width > 0 && r.height > 0);
    }

    function visible(el) {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0)
            return false;
        return el.checkVisibility ? el.checkVisibility({visibilityProperty: true}) : true;
    }

    function inView(r) {
        return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
    }

    function norm(s) {
        return (s || '').replace(/\s+/g, ' ').trim();
    }

    // Collapsed text of el; with withPos also where each character came from: [textNode, offset].
    function textMap(el, withPos = false, maxChars = Infinity) {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
            acceptNode: n => (n.parentElement && SKIP_TAGS.has(n.parentElement.tagName)
                ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
        });
        const out = [];
        const pos = withPos ? [] : null;
        let space = true;   // drop leading whitespace
        for (let n = walker.nextNode(); n && out.length < maxChars; n = walker.nextNode()) {
            const s = n.data;
            for (let i = 0; i < s.length; i++) {
                const ws = /\s/.test(s[i]);
                if (ws && space)
                    continue;
                space = ws;
                out.push(ws ? ' ' : s[i]);
                if (pos)
                    pos.push([n, i]);
            }
        }
        if (out[out.length - 1] === ' ') {
            out.pop();
            if (pos)
                pos.pop();
        }
        return {text: out.join(''), pos};
    }

    function labelOf(el) {
        const tag = el.tagName;
        let s = el.getAttribute('aria-label') || '';
        if (!s && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'))
            s = el.labels?.[0]?.innerText || el.placeholder || (el.type !== 'password' ? el.value : '') || '';
        if (!s && tag === 'IMG')
            s = el.alt || '';
        if (!s)
            s = el.innerText || el.textContent || '';
        if (!s)
            s = el.title || '';
        return norm(s).slice(0, 300);
    }

    function describe(el) {
        const r = el.getBoundingClientRect();
        const d = {
            id: idOf(el), tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
            text: labelOf(el), rect: rectOf(r), rects: rectsOf(el), in_view: inView(r),
        };
        if (el.href)
            d.href = String(el.href);
        return d;
    }

    function byText(q, root) {
        q = norm(q).toLowerCase();
        const hits = new Set();
        // 1) text nodes containing it
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            if (n.parentElement && !SKIP_TAGS.has(n.parentElement.tagName)
                    && norm(n.data).toLowerCase().includes(q))
                hits.add(n.parentElement);
        }
        // 2) labels from attributes, and controls whose text is split over several nodes
        for (const el of root.querySelectorAll(`[aria-label], [placeholder], [alt], [title], ${INTERACTIVE}`)) {
            if (labelOf(el).toLowerCase().includes(q))
                hits.add(el);
        }
        // Prefer the control the text belongs to ("Sign in" span -> its button).
        const out = new Set();
        for (const el of hits)
            out.add(el.closest(INTERACTIVE) || el);
        return Array.from(out);
    }

    function getElements({selector, role, text, limit = 20, visible_only = true}) {
        const root = document.body || document.documentElement;
        let list;
        if (selector)
            list = Array.from(root.querySelectorAll(selector));
        else if (role)
            list = Array.from(root.querySelectorAll(ROLES[role] || `[role="${CSS.escape(role)}"]`));
        else if (text)
            list = byText(text, root);
        else
            throw new Error('get_elements needs selector, role or text');
        if (text && (selector || role)) {
            const q = norm(text).toLowerCase();
            list = list.filter(el => labelOf(el).toLowerCase().includes(q));
        }
        if (visible_only)
            list = list.filter(visible);
        let items = list.map(describe);
        if (text) {
            const q = norm(text).toLowerCase();
            const key = d => (d.text.toLowerCase() === q ? 0 : 2) + (d.in_view ? 0 : 1);
            items = items.map((d, i) => [key(d), i, d]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(x => x[2]);
        }
        return {elements: items.slice(0, limit), total: items.length, view: view()};
    }

    function getParagraphs({min_chars = 40, limit = 200}) {
        const body = document.body || document.documentElement;
        let root = document.querySelector('article, main, [role=main]') || body;
        if (!root.querySelector('p'))
            root = body;
        const out = [];
        for (const p of root.querySelectorAll('p')) {
            if (out.length >= limit)
                break;
            if (!visible(p))
                continue;
            const text = textMap(p).text;
            if (text.length < min_chars)
                continue;
            const r = p.getBoundingClientRect();
            out.push({id: idOf(p), text, rect: rectOf(r), in_view: inView(r)});
        }
        return {paragraphs: out, view: view()};
    }

    function selectRange({element_id, start, end}) {
        const el = byId(element_id);
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            el.focus();
            el.setSelectionRange(start, end);
            return {text: el.value.slice(start, end), rects: rectsOf(el), view: view()};
        }
        const {text, pos} = textMap(el, true);
        start = Math.max(0, Math.min(start, text.length));
        end = Math.max(0, Math.min(end, text.length));
        if (end <= start)
            throw new Error(`empty range ${start}..${end} (text has ${text.length} chars)`);
        const range = document.createRange();
        range.setStart(pos[start][0], pos[start][1]);
        range.setEnd(pos[end - 1][0], pos[end - 1][1] + 1);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        return {text: text.slice(start, end), rects: rectsOf(range), rect: rectOf(range.getBoundingClientRect()),
            view: view()};
    }

    function frames(n) {
        return new Promise(resolve => {
            const step = () => (n-- <= 0 ? resolve() : requestAnimationFrame(step));
            step();
        });
    }

    async function scrollIntoView({element_id, block = 'center'}) {
        const el = byId(element_id);
        el.scrollIntoView({block, inline: 'nearest', behavior: 'instant'});
        await frames(2);   // let layout settle before reading rects
        const r = el.getBoundingClientRect();
        return {rect: rectOf(r), rects: rectsOf(el), in_view: inView(r), view: view()};
    }

    function click({element_id}) {
        const el = byId(element_id);
        const opts = {bubbles: true, cancelable: true, view: window};
        el.dispatchEvent(new MouseEvent('mousedown', opts));
        if (el.focus)
            el.focus();
        el.dispatchEvent(new MouseEvent('mouseup', opts));
        el.click();
        return {clicked: element_id, url: location.href};
    }

    function domText({element_id, max_chars = 20000}) {
        const el = element_id ? byId(element_id) : (document.body || document.documentElement);
        const {text} = textMap(el, false, max_chars);
        return {text, title: document.title, url: location.href};
    }

    const HANDLERS = {
        get_viewport: () => ({view: view()}),
        get_elements: getElements,
        get_paragraphs: getParagraphs,
        select_range: selectRange,
        clear_selection: () => {
            window.getSelection().removeAllRanges();
            return {};
        },
        scroll_into_view: scrollIntoView,
        click,
        dom_text: domText,
    };

    browser.runtime.onMessage.addListener(msg => {
        if (!msg || !msg.kiku)
            return undefined;
        const fn = HANDLERS[msg.kiku];
        if (!fn)
            return Promise.reject(new Error(`unknown page command ${msg.kiku}`));
        return Promise.resolve().then(() => fn(msg.args || {}));
    });
}
