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

    // ---- main content blocks (page reader) ----
    // Simple readability heuristic: pick the main content root, then take p / h1-h6 / li /
    // blockquote that are visible, not inside nav/ads/sidebars/infoboxes, and not mostly links.

    const SKIP_CONTAINERS = 'nav, header, footer, aside, form, table, figure, figcaption, dialog, menu, ' +
        '[role=navigation], [role=banner], [role=contentinfo], [role=complementary], [role=search], ' +
        '[role=dialog], [role=menu], [aria-hidden=true], [hidden]';
    // class/id tokens (split on - _ space) that mark boilerplate
    const SKIP_TOKENS = new Set(['nav', 'navbar', 'navbox', 'navigation', 'menu', 'sidebar', 'side',
        'footer', 'header', 'masthead', 'breadcrumb', 'breadcrumbs', 'ad', 'ads', 'advert', 'advertisement',
        'adslot', 'sponsor', 'sponsored', 'promo', 'promoted', 'share', 'sharing', 'social', 'related',
        'recommended', 'newsletter', 'subscribe', 'cookie', 'cookies', 'consent', 'banner', 'popup', 'modal',
        'infobox', 'sidebox', 'toc', 'reflist', 'references', 'reference', 'citation', 'hatnote', 'metadata',
        'catlinks', 'mw-editsection', 'editsection', 'thumb', 'thumbcaption', 'caption', 'noprint',
        'shortdescription', 'sr-only', 'visually-hidden', 'skip', 'byline', 'tags', 'pagination']);
    const BLOCK_SEL = 'p, h1, h2, h3, h4, h5, h6, li, blockquote, [role=heading]';
    const skipCache = new WeakMap();

    function tokensOf(el) {
        const s = `${typeof el.className === 'string' ? el.className : ''} ${el.id || ''}`.toLowerCase();
        return s.split(/[\s_]+/).flatMap(t => [t, ...t.split('-')]).filter(Boolean);
    }

    function skipped(el, root) {
        // true if el or an ancestor (below root) is boilerplate
        for (let e = el; e && e !== root; e = e.parentElement) {
            if (skipCache.has(e)) {
                if (skipCache.get(e))
                    return true;
                continue;
            }
            const bad = e.matches(SKIP_CONTAINERS) || tokensOf(e).some(t => SKIP_TOKENS.has(t));
            skipCache.set(e, bad);
            if (bad)
                return true;
        }
        return false;
    }

    function linkDensity(el, len) {
        let n = 0;
        for (const a of el.querySelectorAll('a'))
            n += norm(a.textContent).length;
        return len ? n / len : 0;
    }

    function pTextLen(root) {
        let n = 0;
        for (const p of root.querySelectorAll('p'))
            n += norm(p.textContent).length;
        return n;
    }

    function contentRoot() {
        const body = document.body || document.documentElement;
        for (const sel of ['#mw-content-text', 'main', '[role=main]']) {
            const el = document.querySelector(sel);
            if (el && pTextLen(el) > 200)
                return el;
        }
        // the <article> with the most paragraph text (news), else the best-scoring parent (readability)
        let best = null, bestLen = 0;
        for (const a of document.querySelectorAll('article')) {
            const n = pTextLen(a);
            if (n > bestLen)
                [best, bestLen] = [a, n];
        }
        if (best && bestLen > 200)
            return best;
        const score = new Map();
        for (const p of body.querySelectorAll('p')) {
            const n = norm(p.textContent).length;
            if (n < 80)
                continue;
            for (const [e, w] of [[p.parentElement, 1], [p.parentElement?.parentElement, 0.5]])
                if (e)
                    score.set(e, (score.get(e) || 0) + n * w);
        }
        best = body;
        bestLen = 0;
        for (const [e, n] of score)
            if (n > bestLen)
                [best, bestLen] = [e, n];
        return best;
    }

    function getBlocks({min_p_chars = 40, min_li_chars = 25, max_blocks = 400, max_chars = 80000}) {
        const root = contentRoot();
        const out = [];
        let total = 0;
        for (const el of root.querySelectorAll(BLOCK_SEL)) {
            if (out.length >= max_blocks || total >= max_chars)
                break;
            const tag = el.tagName;
            const heading = /^H[1-6]$/.test(tag) || el.getAttribute('role') === 'heading';
            // nested blocks: take the inner one (li > p, blockquote > p), skip the wrapper
            if (!heading && el.querySelector('p, li, blockquote, h1, h2, h3, h4, h5, h6'))
                continue;
            if (!heading && el.parentElement?.closest('p, h1, h2, h3, h4, h5, h6'))
                continue;
            if (skipped(el, root) || !visible(el))
                continue;
            const text = textMap(el).text;
            const kind = heading ? 'h' : (tag === 'LI' ? 'li' : 'p');
            if (kind === 'h' && (text.length < 2 || text.length > 200))
                continue;
            if (kind === 'p' && (text.length < min_p_chars || !text.includes(' ')))
                continue;
            if (kind === 'li' && text.length < min_li_chars)
                continue;
            if (kind !== 'h' && linkDensity(el, text.length) > (kind === 'li' ? 0.5 : 0.8))
                continue;
            const r = el.getBoundingClientRect();
            const level = heading ? (Number(tag[1]) || Number(el.getAttribute('aria-level')) || 2) : 0;
            out.push({id: idOf(el), kind, level, text, rect: rectOf(r), in_view: inView(r)});
            total += text.length;
        }
        return {blocks: out, title: document.title, url: location.href, view: view()};
    }

    // Visual lines of an element: [{start, end, rect}] with offsets into textMap(el).text.
    function lineRanges({element_id, max_lines = 60}) {
        const el = byId(element_id);
        const {text, pos} = textMap(el, true);
        const lines = [];
        let cur = null;
        const range = document.createRange();
        for (let i = 0; i < text.length && lines.length < max_lines; i++) {
            if (i > 0 && text[i - 1] !== ' ')
                continue;   // word starts only
            range.setStart(pos[i][0], pos[i][1]);
            range.setEnd(pos[i][0], pos[i][1] + 1);
            const r = range.getBoundingClientRect();
            if (r.height <= 0)
                continue;
            if (!cur || r.top > cur.top + r.height / 2) {
                if (cur)
                    lines.push({start: cur.start, end: i});
                cur = {start: i, top: r.top};
            }
        }
        if (cur && lines.length < max_lines)
            lines.push({start: cur.start, end: text.length});
        for (const ln of lines) {
            while (ln.end > ln.start && text[ln.end - 1] === ' ')
                ln.end--;
            range.setStart(pos[ln.start][0], pos[ln.start][1]);
            range.setEnd(pos[ln.end - 1][0], pos[ln.end - 1][1] + 1);
            ln.rect = rectOf(range.getBoundingClientRect());
        }
        return {lines: lines.filter(l => l.end > l.start), view: view()};
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

    // inner: rendered text (el.innerText): skips hidden parts and keeps line breaks.
    function domText({element_id, max_chars = 20000, inner = false}) {
        const el = element_id ? byId(element_id) : (document.body || document.documentElement);
        const text = inner ? (el.innerText || '').slice(0, max_chars) : textMap(el, false, max_chars).text;
        return {text, title: document.title, url: location.href};
    }

    // ---- rows (Gmail list): selectors come from Python so site changes are fixed in one place ----

    function fieldOf(row, specs) {
        // specs: [[selector, attr|null], ...]; '' = the row itself. First non-empty value wins.
        for (const [sel, attr] of specs) {
            let el;
            try {
                el = sel ? row.querySelector(sel) : row;
            } catch {
                continue;   // bad selector: try the next one
            }
            if (!el)
                continue;
            const v = norm(attr ? el.getAttribute(attr) : (el.innerText || el.textContent));
            if (v)
                return [v, rectOf(el.getBoundingClientRect())];
        }
        return [null, null];
    }

    // row: selector or list of selectors (first one with visible matches wins).
    function getRows({row, fields = {}, limit = 50}) {
        let list = [], used = null;
        for (const sel of Array.isArray(row) ? row : [row]) {
            try {
                list = Array.from(document.querySelectorAll(sel)).filter(visible);
            } catch {
                list = [];
            }
            if (list.length) {
                used = sel;
                break;
            }
        }
        const rows = list.slice(0, limit).map(el => {
            const r = el.getBoundingClientRect();
            const d = {
                id: idOf(el), rect: rectOf(r), in_view: inView(r), fields: {}, field_rects: {},
                lines: (el.innerText || '').split('\n').map(norm).filter(Boolean).slice(0, 12),
            };
            for (const [name, specs] of Object.entries(fields)) {
                const [v, fr] = fieldOf(el, specs);
                d.fields[name] = v;
                if (fr)
                    d.field_rects[name] = fr;
            }
            return d;
        });
        return {rows, total: list.length, selector: used, view: view()};
    }

    function getFocus() {
        const el = document.activeElement;
        const has = document.hasFocus();
        if (!el || el === document.body || el === document.documentElement)
            return {has_focus: has, element: null, view: view()};
        return {has_focus: has, element: describe(el), view: view()};
    }

    const HANDLERS = {
        get_viewport: () => ({view: view()}),
        get_elements: getElements,
        get_paragraphs: getParagraphs,
        get_blocks: getBlocks,
        line_ranges: lineRanges,
        select_range: selectRange,
        clear_selection: () => {
            window.getSelection().removeAllRanges();
            return {};
        },
        scroll_into_view: scrollIntoView,
        click,
        dom_text: domText,
        get_rows: getRows,
        get_focus: getFocus,
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
