"""page_reader: explain the current Firefox tab (a paragraph, the current section, the whole page)
and answer questions about it, with follow-ups.

Text: the extension's get_blocks (main content p / h / li, nav/ads/sidebars/infoboxes skipped).
RAG: blocks -> ~300-token chunks -> BM25 (rank_bm25) -> top 3 chunks -> qwen. Parsed pages are
cached per URL (refetched when the tab reloads).
Visuals: scroll the paragraph into view, move Kiku's icon to it, then select it line by line
(select_range; scan_rect if selection fails) while the LLM thinks; clear; show the answer as a caption.

Pure helpers (chunk, Doc, sections, copies, ...) need no browser: tests/test_page_reader.py.
"""
import collections
import re
import threading
import time
import types

import config
import perf
from browser.bridge import BridgeError
from browser.page import Page
from control.actions import Aborted
from tools import open_website
from tools.registry import register, sorry

_shell = _actions = _bridge = _llm = _page = None
_cache = collections.OrderedDict()   # url -> Doc
_last = {"url": None, "chunks": [], "q": None, "a": None}   # for follow-up questions

PAGE_TOOLS = {"analyze_paragraph", "analyze_section", "analyze_page", "ask_page"}


def _log(msg):
    print(f"[KIKU] page_reader: {msg}", flush=True)


def init(shell, actions, bridge, llm):
    global _shell, _actions, _bridge, _llm, _page
    _shell, _actions, _bridge, _llm = shell, actions, bridge, llm
    _page = Page(bridge, shell, actions)


# ---- text helpers ----

_STOP = set("""a an the and or but if then than so of to in on at by for with from into onto about as is are
was were be been being it its this that these those there here what which who whom whose when where why how
do does did doing done have has had can could would should will shall may might must not no yes i me my we
our you your he him his she her they them their page article post text say says said tell mention
mentions mentioned talk talks write writes wrote written explain please kiku also just more any some""".split())


def clean(text):
    """Page text for the LLM: drop citation marks like [12], [citation needed], [edit]."""
    text = re.sub(r"\[(?:\d+|[a-z]|citation needed|edit|note \d+)\]", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def est_tokens(text):
    return int(len(text.split()) * 1.3) + 1


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def truncate(text, max_tokens):
    """Whole sentences up to max_tokens (at least the first one, cut by words)."""
    out, n = [], 0
    for s in _sentences(text):
        t = est_tokens(s)
        if out and n + t > max_tokens:
            break
        out.append(s)
        n += t
    text = " ".join(out)
    words = text.split()
    limit = int(max_tokens / 1.3)
    return " ".join(words[:limit]) if len(words) > limit else text


def copies(answer, source, n=8):
    """True if answer repeats n words in a row from source."""
    a, s = re.findall(r"[a-z0-9']+", answer.lower()), re.findall(r"[a-z0-9']+", source.lower())
    grams = {tuple(s[i:i + n]) for i in range(len(s) - n + 1)}
    return any(tuple(a[i:i + n]) in grams for i in range(len(a) - n + 1))


# ---- page structure ----

def paragraphs(blocks):
    """Indices of real content paragraphs (kind p; short/empty <p> were dropped by the extension)."""
    return [i for i, b in enumerate(blocks) if b["kind"] == "p"]


def nth(items, n):
    """1-based n (-1 = last) -> item, or None."""
    if n == -1 and items:
        return items[-1]
    return items[n - 1] if 1 <= n <= len(items) else None


def section_of(blocks, i):
    """(start, end) block range of the section holding block i: from its heading to the next
    heading of the same or higher level. start is the heading index (or 0 with no heading)."""
    start = next((j for j in range(i, -1, -1) if blocks[j]["kind"] == "h"), None)
    if start is None:
        end = next((j for j in range(i, len(blocks)) if blocks[j]["kind"] == "h"), len(blocks))
        return 0, end
    level = blocks[start]["level"] or 6
    end = next((j for j in range(start + 1, len(blocks))
                if blocks[j]["kind"] == "h" and (blocks[j]["level"] or 6) <= level), len(blocks))
    return start, end


def heading_of(blocks, i):
    j = next((j for j in range(i, -1, -1) if blocks[j]["kind"] == "h"), None)
    return blocks[j]["text"] if j is not None else ""


def current_block(blocks, view):
    """Index of the content block the user is looking at: the first one in view that reaches
    below the top fifth of the viewport. None if nothing is in view."""
    top = float(view.get("inner_height", 800)) * 0.2
    seen = [i for i, b in enumerate(blocks) if b.get("in_view")]
    for i in seen:
        r = blocks[i]["rect"]
        if r["y"] + r["height"] > top:
            return i
    return seen[0] if seen else None


def block_text(b):
    t = clean(b["text"])
    return f"- {t}" if b["kind"] == "li" else t


def join_blocks(blocks, start, end, max_tokens):
    """Text of blocks[start:end], headings as '## ...', cut at max_tokens."""
    out, n = [], 0
    for b in blocks[start:end]:
        t = f"## {clean(b['text'])}" if b["kind"] == "h" else block_text(b)
        k = est_tokens(t)
        if n + k > max_tokens:
            if not out:
                out.append(truncate(t, max_tokens))
            break
        out.append(t)
        n += k
    return "\n".join(out)


def chunk(blocks, max_tokens=None):
    """Blocks -> chunks [{text, heading, blocks: [block indices]}], ~max_tokens each.
    A heading starts a new chunk; a block longer than max_tokens is split by sentences."""
    max_tokens = max_tokens or config.PAGE_CHUNK_TOKENS
    chunks, cur, heading = [], None, ""

    def flush():
        if cur and cur["parts"]:
            chunks.append({"text": "\n".join(cur["parts"]), "heading": cur["heading"], "blocks": cur["blocks"]})

    for i, b in enumerate(blocks):
        if b["kind"] == "h":
            flush()
            heading, cur = clean(b["text"]), None
            continue
        pieces, buf, n = [], [], 0
        for s in _sentences(block_text(b)):   # split long blocks into ~max_tokens pieces
            k = est_tokens(s)
            if buf and n + k > max_tokens:
                pieces.append(" ".join(buf))
                buf, n = [], 0
            buf.append(s)
            n += k
        if buf:
            pieces.append(" ".join(buf))
        for p in pieces:
            k = est_tokens(p)
            if cur is None or cur["n"] + k > max_tokens:
                flush()
                cur = {"parts": [], "heading": heading, "blocks": [], "n": 0}
            cur["parts"].append(p)
            cur["n"] += k
            if not cur["blocks"] or cur["blocks"][-1] != i:
                cur["blocks"].append(i)
    flush()
    return chunks


class Doc:
    """One parsed page: blocks, chunks, BM25 index."""

    def __init__(self, url, title, blocks, load_seq=0):
        from rank_bm25 import BM25Okapi
        self.url, self.title, self.blocks, self.load_seq = url, title, blocks, load_seq
        self.chunks = chunk(blocks)
        self._toks = [tokens(f"{c['heading']} {c['text']}") for c in self.chunks]
        self.bm25 = BM25Okapi([t or ["_"] for t in self._toks]) if self.chunks else None

    def search(self, query, k=None):
        """Top k chunks that share at least one word with query, best first."""
        q = tokens(query)
        if not q or self.bm25 is None:
            return []
        scores = self.bm25.get_scores(q)
        qs = set(q)
        hits = [i for i in range(len(self.chunks)) if qs & set(self._toks[i])]
        hits.sort(key=lambda i: -scores[i])
        return [self.chunks[i] for i in hits[:k or config.PAGE_TOP_K]]


# ---- browser ----

def _load_doc():
    """Doc for the active tab, from the cache unless the URL changed or the tab reloaded."""
    info = _bridge.call("get_url", timeout=2)
    url, seq = info["url"], info.get("load_seq", 0)
    doc = _cache.get(url)
    if doc is not None and doc.load_seq == seq:
        _cache.move_to_end(url)
        return doc
    t = time.monotonic()
    res = _page.blocks(min_p_chars=config.PAGE_MIN_P_CHARS)
    doc = Doc(url, res.get("title") or info.get("title", ""), res["blocks"], seq)
    _cache[url] = doc
    while len(_cache) > config.PAGE_CACHE_PAGES:
        _cache.popitem(last=False)
    n_p = len(paragraphs(doc.blocks))
    _log(f"read {url}: {len(doc.blocks)} blocks ({n_p} paragraphs), {len(doc.chunks)} chunks "
         f"in {(time.monotonic() - t) * 1000:.0f} ms")
    return doc


def _check_ready():
    """None if we can read the page, else what to say. Brings Firefox to the front first."""
    if _page is None:
        return "Page reading isn't set up."
    if _llm is None or not _llm.available:
        return "Sorry, my language model is offline."
    _actions.begin()
    try:
        with perf.stage("focus firefox"):
            open_website.focus_firefox()   # don't read it from behind the terminal
    except Aborted:
        return "Stopped."
    except RuntimeError as e:
        _log(f"can't bring Firefox up: {e}")
        return sorry(e, "I can't bring Firefox up")
    if not _bridge.connected:
        _log("waiting for the Firefox extension to connect")
        if not _bridge.wait_connected(config.PAGE_CONNECT_WAIT_S):   # it retries every 1-5 s
            return "The Firefox extension isn't connected, so I can't read the page."
    return None


# ---- visuals ----

def _wait_done(done, seconds):
    """Wait up to seconds or until done; abortable."""
    end = time.monotonic() + seconds
    while not done.is_set():
        _actions.check()
        left = end - time.monotonic()
        if left <= 0:
            return
        done.wait(min(0.05, left))
    _actions.check()


def _show(el):
    """Scroll el into view and move Kiku's icon to its first line."""
    _page.scroll_into_view(el)
    vp = el["viewport"]
    if el["target"].h > 0.7 * vp.height:   # tall block: show its start, not its middle
        _page.scroll_into_view(el, block="start")
    t = el["target"]
    _actions.point_at((t.x, t.y + min(t.h / 2, 14)))


def _scan(el, done):
    """Select el line by line until done; then keep the whole visible part selected."""
    try:
        lines = [ln for ln in _page.line_ranges(el) if ln["in_view"]]
    except BridgeError as e:
        _log(f"line_ranges failed ({e}); using scan_rect")
        lines = []
    try:
        for ln in lines:
            if done.is_set():
                return
            _page.select(el, ln["start"], ln["end"])
            t = ln["target"]
            _shell.move_to(t.x, t.y + t.h / 2, wait=False)
            _wait_done(done, config.SCAN_LINE_MS / 1000)
        if lines:
            _page.select(el, lines[0]["start"], lines[-1]["end"])
            _wait_done(done, 3600)
            return
    except BridgeError as e:
        _log(f"select_range failed ({e}); using scan_rect")
    _scan_rect(el["target"].rect, done)


def _scan_rect(rect, done):
    while not done.is_set():
        _shell.scan_rect(*rect, config.SCAN_RECT_MS, wait=False)
        _wait_done(done, config.SCAN_RECT_MS / 1000)


def _clear():
    try:
        _page.clear_selection()
    except BridgeError:
        pass
    _shell.clear_highlight()


# ---- answering ----

SYSTEM = ("You are Kiku, explaining a web page out loud to the user. Answer in two or three short "
          "sentences, in your own words. Show that you understand it: say what it means, why it matters, "
          "or how it fits together. Never copy or quote sentences from the page. No lists, markdown, or emoji.")
ASK_SYSTEM = SYSTEM + (" Answer the question using only the page text you are given. If the text doesn't "
                       "answer it, say the page doesn't mention it.")
REPHRASE = " Your last answer copied the page word for word; say it completely in your own words."


def _llm_answer(system, prompt, source, history=()):
    n, mt = config.PAGE_ANSWER_SENTENCES, config.PAGE_MAX_TOKENS
    a = _llm.ask(system, prompt, history, sentences=n, max_tokens=mt)
    if copies(a, source, config.PAGE_COPY_WORDS):
        _log("answer copied the page; asking again")
        a = _llm.ask(system + REPHRASE, prompt, history, sentences=n, max_tokens=mt, temperature=0.7)
    return a


def _run(ctx, doc, el, system, prompt, source, history=()):
    """Ask the LLM in the background while showing el (or scanning the viewport); return the answer."""
    _shell.show_caption("")
    done, out = threading.Event(), {}

    def work():
        try:
            out["a"] = _llm_answer(system, prompt, source, history)
        except Exception as e:
            _log(f"LLM failed: {e!r}")
        finally:
            done.set()

    t = time.monotonic()
    threading.Thread(target=work, name="kiku-page-llm", daemon=True).start()
    try:
        try:
            if el is not None:
                with perf.stage("scroll + point"):
                    _show(el)
                with perf.stage("scan (llm runs)"):
                    _scan(el, done)
            else:
                with perf.stage("scan (llm runs)"):
                    _scan_view(done)
        except BridgeError as e:
            _log(f"visuals skipped: {e}")
        with perf.stage("wait for llm"):
            _wait_done(done, 3600)
    except Aborted:
        _log("aborted")
        return "Stopped."
    finally:
        _clear()
    ctx.focus["page"] = doc.url
    a = out.get("a")
    if not a:
        return "Sorry, I couldn't work that out."
    _log(f"answered in {(time.monotonic() - t) * 1000:.0f} ms")
    _shell.show_caption(a)
    return a


def explain(ctx, url, el, system, prompt, source):
    """Scan el while the LLM answers, caption the answer (used by tools/gmail.py for long emails)."""
    return _run(ctx, types.SimpleNamespace(url=url), el, system, prompt, source)


def _scan_view(done):
    res = _bridge.call("get_viewport")
    vp = _page.viewport(res["view"])
    _actions.point_at((vp.ox + 24, vp.oy + 24))
    _scan_rect((vp.ox, vp.oy, vp.width, vp.height), done)


def _remember(doc, chunks, q, a):
    if a and a != "Stopped." and not a.startswith("Sorry"):
        _last.update(url=doc.url, chunks=chunks, q=q, a=a)


def _verb(mode):
    return "Summarize" if mode == "summarize" else "Explain what it says and what it means:"


def _start():
    """(doc, None) or (None, what to say)."""
    msg = _check_ready()
    if msg:
        return None, msg
    try:
        with perf.stage("read page"):
            doc = _load_doc()
    except BridgeError as e:
        _log(f"can't read the page: {e}")
        return None, "I can't read this page."
    if not doc.blocks:
        return None, "I can't find any readable text on this page."
    return doc, None


# ---- tools ----

@register("analyze_paragraph", "Analyze or summarize the nth paragraph of the current web page (-1 = last)",
          {"n": {"type": "integer"}, "mode": {"type": "string", "enum": ["analyze", "summarize"]}})
def analyze_paragraph(ctx, n, mode="analyze"):
    doc, msg = _start()
    if msg:
        return msg
    paras = paragraphs(doc.blocks)
    i = nth(paras, n)
    if i is None:
        return f"This page has only {len(paras)} paragraphs." if paras else "I can't find paragraphs on this page."
    b = doc.blocks[i]
    text = truncate(clean(b["text"]), config.PAGE_CONTEXT_TOKENS)
    head = heading_of(doc.blocks, i)
    prompt = (f"Page: {doc.title}\n" + (f"Section: {head}\n" if head else "") + f"\nParagraph:\n{text}\n\n"
              f"{_verb(mode)} this paragraph.")
    a = _run(ctx, doc, b, SYSTEM, prompt, text)
    _remember(doc, [{"text": text, "heading": head, "blocks": [i]}], f"paragraph {n}", a)
    return a


@register("analyze_section", "Analyze or summarize the section of the web page the user is looking at",
          {"mode": {"type": "string", "enum": ["analyze", "summarize"]}})
def analyze_section(ctx, mode="analyze"):
    doc, msg = _start()
    if msg:
        return msg
    try:   # where is the user now? (fresh in_view flags, same element ids as the cached doc)
        res = _page.blocks(min_p_chars=config.PAGE_MIN_P_CHARS)
        seen = {b["id"]: b for b in res["blocks"]}
        for b in doc.blocks:
            if b["id"] in seen:
                b.update(rect=seen[b["id"]]["rect"], in_view=seen[b["id"]]["in_view"])
        i = current_block(doc.blocks, res["view"])
    except BridgeError as e:
        _log(f"can't tell what's in view: {e}")
        i = None
    if i is None:
        i = 0
    start, end = section_of(doc.blocks, i)
    head = doc.blocks[start]["text"] if doc.blocks[start]["kind"] == "h" else ""
    text = join_blocks(doc.blocks, start, end, config.PAGE_CONTEXT_TOKENS)
    el_i = i if doc.blocks[i]["kind"] != "h" else next(
        (j for j in range(start, end) if doc.blocks[j]["kind"] != "h"), i)
    prompt = (f"Page: {doc.title}\n\nSection{f' {head!r}' if head else ''}:\n{text}\n\n"
              f"{_verb(mode)} this section.")
    a = _run(ctx, doc, doc.blocks[el_i], SYSTEM, prompt, text)
    _remember(doc, [{"text": text, "heading": head, "blocks": list(range(start, end))}], "this section", a)
    return a


@register("analyze_page", "Summarize or explain the whole current web page",
          {"mode": {"type": "string", "enum": ["analyze", "summarize"]}})
def analyze_page(ctx, mode="summarize"):
    doc, msg = _start()
    if msg:
        return msg
    heads = [clean(b["text"]) for b in doc.blocks if b["kind"] == "h" and (b["level"] or 6) <= 2][:12]
    budget = config.PAGE_CONTEXT_TOKENS - est_tokens(" ".join(heads))
    lead, n = [], 0
    for c in doc.chunks:   # the start of the page carries the gist (lead section, first post)
        k = est_tokens(c["text"])
        if lead and n + k > budget:
            break
        lead.append(c)
        n += k
    text = truncate("\n".join(c["text"] for c in lead), budget)
    prompt = (f"Page: {doc.title}\n" + (f"Sections: {'; '.join(heads)}\n" if heads else "")
              + f"\nStart of the page:\n{text}\n\n"
              + ("Summarize what this page is about." if mode == "summarize"
                 else "Explain what this page is about and its main point."))
    a = _run(ctx, doc, None, SYSTEM, prompt, text)
    _remember(doc, lead, "this page", a)
    return a


@register("ask_page", "Answer a question about the current web page (what does it say about X, follow-ups)",
          {"question": {"type": "string"}})
def ask_page(ctx, question):
    doc, msg = _start()
    if msg:
        return msg
    followup = _last["url"] == doc.url and bool(_last["chunks"])
    chunks = doc.search(question)
    words = tokens(question)
    if not chunks and followup and len(words) <= 2:
        chunks = _last["chunks"]   # "why is that?", "in simpler terms" -> same text as the last answer
    if not chunks:
        if len(words) >= 2:
            _log("question isn't about this page; chatting")
            return _llm.chat(question, ctx)
        return "The page doesn't seem to mention that."
    parts, n = [], 0
    for c in chunks:
        t = (f"[{c['heading']}] " if c["heading"] else "") + c["text"]
        k = est_tokens(t)
        if parts and n + k > config.PAGE_CONTEXT_TOKENS:
            break
        parts.append(truncate(t, config.PAGE_CONTEXT_TOKENS))
        n += k
    text = "\n\n".join(parts)
    history = ([{"role": "user", "content": _last["q"]}, {"role": "assistant", "content": _last["a"]}]
               if followup and _last["q"] and _last["a"] else [])
    prompt = f"Page: {doc.title}\n\nPage text:\n{text}\n\nQuestion: {question}"
    q = set(tokens(question))
    best = chunks[0]["blocks"]   # show the paragraph that matches best
    el_i = next((i for i in best if doc.blocks[i]["kind"] != "h" and q & set(tokens(doc.blocks[i]["text"]))),
                best[0])
    a = _run(ctx, doc, doc.blocks[el_i], ASK_SYSTEM, prompt, text, history)
    _remember(doc, chunks, question, a)
    return a
