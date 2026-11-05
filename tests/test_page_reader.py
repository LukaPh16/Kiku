"""Page reader tests without Firefox or Ollama: chunking, BM25, paragraph/section picking, the
verbatim check, router rules and follow-ups, and the tools end to end against fakes.

  venv/bin/python -m tests.test_page_reader
"""
import threading

from brain.context import Context
from brain.router import Router, match_rules
from control.locate import Target
from tools import page_reader as pr


def log(msg):
    print(f"[KIKU] test: {msg}", flush=True)


def blk(i, kind, text, level=0, y=None):
    y = i * 100 if y is None else y
    return {"id": f"k{i}", "kind": kind, "level": level, "text": text,
            "rect": {"x": 10, "y": y, "width": 600, "height": 80}, "in_view": 0 <= y < 800}


LEAD = ("The Roman Empire was the state ruled by the Romans after the fall of the Republic in 27 BC. "
        "At its height it controlled the Mediterranean and much of Europe.[3]")
ARMY = ("The legions were the core of the Roman army, made of heavy infantry recruited from citizens. "
        "Soldiers served for twenty five years and were paid in salt and coins.")
FALL = ("The western half fell in 476 AD when Germanic tribes deposed the last emperor. "
        "Historians blame economic decline, plague and pressure on the borders.")
BLOCKS = [
    blk(0, "p", LEAD),
    blk(1, "h", "Military", 2),
    blk(2, "p", ARMY),
    blk(3, "li", "Legions of about five thousand men"),
    blk(4, "h", "Navy", 3),
    blk(5, "p", "The navy kept the sea free of pirates and moved grain from Egypt to Rome."),
    blk(6, "h", "Decline", 2),
    blk(7, "p", FALL),
]


def test_clean_and_tokens():
    assert pr.clean("Rome.[3] Big[citation needed] city [edit]") == "Rome. Big city"
    assert pr.tokens("What does the page say about the legions?") == ["legion"]
    assert pr.est_tokens("one two three") == 4


def test_paragraphs_and_nth():
    paras = pr.paragraphs(BLOCKS)
    assert paras == [0, 2, 5, 7]
    assert pr.nth(paras, 1) == 0 and pr.nth(paras, -1) == 7 and pr.nth(paras, 9) is None


def test_sections():
    assert pr.section_of(BLOCKS, 0) == (0, 1)       # lead: before the first heading
    assert pr.section_of(BLOCKS, 2) == (1, 6)       # Military includes its h3 Navy subsection
    assert pr.section_of(BLOCKS, 5) == (4, 6)       # Navy
    assert pr.section_of(BLOCKS, 7) == (6, 8)
    assert pr.heading_of(BLOCKS, 3) == "Military"
    view = {"inner_height": 800}
    moved = [dict(b, in_view=b["id"] in ("k4", "k5"), rect=dict(b["rect"], y=b["rect"]["y"] - 400)) for b in BLOCKS]
    assert pr.current_block(moved, view) == 5        # the heading at the very top is passed; reading k5
    assert pr.current_block([dict(b, in_view=False) for b in BLOCKS], view) is None


def test_chunks():
    ch = pr.chunk(BLOCKS, max_tokens=300)
    assert [c["heading"] for c in ch] == ["", "Military", "Navy", "Decline"]
    assert ch[1]["blocks"] == [2, 3] and "- Legions" in ch[1]["text"]
    long = [blk(0, "p", " ".join(f"Sentence number {i} is about grain." for i in range(80)))]
    ch = pr.chunk(long, max_tokens=60)
    assert len(ch) > 5 and all(pr.est_tokens(c["text"]) <= 70 for c in ch), [pr.est_tokens(c["text"]) for c in ch]
    assert all(c["blocks"] == [0] for c in ch)


def test_search():
    doc = pr.Doc("u", "Roman Empire", BLOCKS)
    assert doc.search("what does it say about the legions?")[0]["heading"] == "Military"
    assert doc.search("what caused the decline?")[0]["heading"] == "Decline"
    assert doc.search("pizza recipes") == []
    assert doc.search("why is that?") == []          # no content words: caller reuses the last chunks


def test_copies_and_truncate():
    assert pr.copies("Well, the legions were the core of the Roman army, it seems.", ARMY)
    assert not pr.copies("Rome relied on long-serving professional infantry units.", ARMY)
    t = pr.truncate(" ".join(["word"] * 1000), 100)
    assert len(t.split()) <= 77


ROUTES = [
    ("Summarize this section.", ("analyze_section", {"mode": "summarize"})),
    ("explain the current section", ("analyze_section", {"mode": "analyze"})),
    ("What is this section about?", ("analyze_section", {"mode": "summarize"})),
    ("Summarize the page.", ("analyze_page", {"mode": "summarize"})),
    ("Analyze this article", ("analyze_page", {"mode": "analyze"})),
    ("What's this page about?", ("analyze_page", {"mode": "summarize"})),
    ("give me the gist of this article", ("analyze_page", {"mode": "summarize"})),
    ("tldr", ("analyze_page", {"mode": "summarize"})),
    ("What does it say about the legions?", ("ask_page", {"question": "What does the page say about the legions?"})),
    ("what does the article say about taxes", ("ask_page", {"question": "What does the page say about taxes?"})),
    ("Does this page mention Egypt?", ("ask_page", {"question": "What does the page say about egypt?"})),
    ("Explain the first paragraph.", ("analyze_paragraph", {"n": 1, "mode": "analyze"})),
]


def test_router_rules():
    bad = []
    for phrase, want in ROUTES:
        r = match_rules(phrase, Context())
        got = (r["tool"], r["args"]) if r else None
        if got != want:
            bad.append((phrase, got, want))
    assert not bad, bad


def test_followups_route_to_page():
    router = Router(llm=None)
    assert router.route("Why is that?")["tool"] == "chat"            # no page answer yet
    router.ctx.add_turn("summarize the page", "It is about Rome.", "analyze_page")
    for q in ["Why is that?", "tell me more", "And what about the navy?", "explain that in simpler terms"]:
        r = router.route(q)
        assert (r["tool"], r["via"], r["args"]) == ("ask_page", "followup", {"question": q}), (q, r)
    assert router.route("open youtube")["tool"] == "open_website"    # commands still win
    router.ctx.add_turn("open youtube", "Opened youtube.", "open_website")
    assert router.route("Why is that?")["tool"] == "chat"


# ---- tools end to end against fakes ----

class FakeBridge:
    connected = True

    def wait_connected(self, timeout):
        return self.connected

    def __init__(self, blocks):
        self.blocks, self.calls, self.seq = blocks, [], 1

    def call(self, cmd, timeout=None, **a):
        self.calls.append((cmd, a))
        view = {"inner_width": 1000, "inner_height": 800, "dpr": 1, "url": "https://x/wiki/Rome"}
        if cmd == "get_url":
            return {"url": "https://x/wiki/Rome", "title": "Rome", "load_seq": self.seq, "status": "complete"}
        if cmd == "get_blocks":
            return {"blocks": [dict(b) for b in self.blocks], "title": "Roman Empire", "url": "u", "view": view}
        if cmd == "scroll_into_view":
            return {"rect": {"x": 10, "y": 300, "width": 600, "height": 80}, "in_view": True, "view": view}
        if cmd == "line_ranges":
            return {"lines": [{"start": 0, "end": 40, "rect": {"x": 10, "y": 300, "width": 600, "height": 20}},
                              {"start": 41, "end": 90, "rect": {"x": 10, "y": 320, "width": 600, "height": 20}}],
                    "view": view}
        if cmd == "select_range":
            return {"text": "x", "rects": [], "view": view}
        if cmd in ("clear_selection",):
            return {}
        if cmd == "get_viewport":
            return {"view": view}
        raise AssertionError(cmd)


class FakeShell:
    def __init__(self):
        self.calls = []

    def list_windows(self):
        return [{"id": 1, "app_id": "firefox", "focused": True, "minimized": False, "on_active_workspace": True,
                 "monitor": 0, "frame": {"x": 0, "y": 0, "width": 1000, "height": 900}}]

    def get_monitors(self):
        return [{"index": 0, "scale": 1}]

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


class FakeActions:
    aborted = False

    def __init__(self):
        self._abort = threading.Event()

    def begin(self):
        self._abort.clear()

    def check(self):
        if self._abort.is_set():
            from control.actions import Aborted
            raise Aborted()

    def point_at(self, target, hover_ms=None):
        self.check()

    def wait(self, s):
        if self._abort.wait(s):
            from control.actions import Aborted
            raise Aborted()


class FakeLlm:
    available = True

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def ask(self, system, prompt, history=(), **kw):
        self.prompts.append((system, prompt, list(history)))
        threading.Event().wait(0.08)   # "thinking" long enough for the line scan to run
        return self.replies.pop(0)

    def chat(self, text, ctx=None):
        return "general answer"


FOCUSED = []   # focus_firefox calls, checked before the page is read


def _setup(replies):
    bridge, shell, actions, llm = FakeBridge(BLOCKS), FakeShell(), FakeActions(), FakeLlm(replies)
    FOCUSED.clear()
    pr.open_website.focus_firefox = lambda: FOCUSED.append(len(bridge.calls))
    pr._cache.clear()
    pr._last.update(url=None, chunks=[], q=None, a=None)
    pr.init(shell, actions, bridge, llm)
    return bridge, shell, actions, llm


def test_tools_end_to_end():
    import config
    config.SCAN_LINE_MS = 10
    bridge, shell, actions, llm = _setup(["Rome was a huge state.", "Its army was professional.",
                                          "Because they served long terms.", "Rome took Egypt's grain."])
    ctx = Context()
    a = pr.analyze_paragraph(ctx, 1, "summarize")
    assert a == "Rome was a huge state." and ctx.focus["page"] == "https://x/wiki/Rome"
    assert FOCUSED == [0], FOCUSED        # Firefox brought up before anything was read
    assert "Paragraph:\n" + LEAD[:40] in llm.prompts[0][1] and "[3]" not in llm.prompts[0][1]
    assert ("show_caption", (a,)) in shell.calls
    cmds = [c for c, _ in bridge.calls]
    assert cmds.count("select_range") >= 2 and cmds[-1] == "clear_selection", cmds
    assert ("scroll_into_view", {"element_id": "k0", "block": "center"}) in bridge.calls

    a = pr.ask_page(ctx, "What does the page say about the legions?")
    assert a == "Its army was professional." and "legions were the core" in llm.prompts[1][1]
    assert cmds.count("get_blocks") == 1 and [c for c, _ in bridge.calls].count("get_blocks") == 1   # cached
    a = pr.ask_page(ctx, "Why is that?")    # follow-up: same chunks + last Q/A as history
    assert "legions were the core" in llm.prompts[2][1]
    assert llm.prompts[2][2][1]["content"] == "Its army was professional."
    assert pr.ask_page(ctx, "Who won the 2022 world cup final?") == "general answer"   # not on this page

    bridge.seq = 2   # tab reloaded -> re-read
    pr.analyze_section(ctx, "summarize")
    assert [c for c, _ in bridge.calls].count("get_blocks") >= 2
    assert pr.analyze_paragraph(ctx, 9, "analyze") == "This page has only 4 paragraphs."


def test_copy_retry_and_abort():
    bridge, shell, actions, llm = _setup(["The legions were the core of the Roman army, made of heavy infantry.",
                                          "Rome's army ran on career foot soldiers."])
    assert pr.analyze_paragraph(Context(), 2, "analyze") == "Rome's army ran on career foot soldiers."
    assert pr.REPHRASE in llm.prompts[1][0]

    slow = threading.Event()

    class SlowLlm(FakeLlm):
        def ask(self, *a, **k):
            slow.wait(2)
            return "late"
    pr.init(shell, actions, bridge, SlowLlm([]))
    threading.Timer(0.15, actions._abort.set).start()
    assert pr.analyze_paragraph(Context(), 1, "analyze") == "Stopped."
    assert bridge.calls[-1][0] == "clear_selection"
    slow.set()


def test_not_connected():
    _setup([])
    pr._bridge.connected = False
    assert "extension isn't connected" in pr.analyze_page(Context())


def test_firefox_wont_focus():
    bridge, *_ = _setup([])

    def fail():
        from control.actions import Failed
        raise Failed("Firefox didn't take focus")
    pr.open_website.focus_firefox = fail
    assert pr.analyze_page(Context()) == "Sorry, Firefox didn't take focus."
    assert bridge.calls == []


if __name__ == "__main__":
    tests = [test_clean_and_tokens, test_paragraphs_and_nth, test_sections, test_chunks, test_search,
             test_copies_and_truncate, test_router_rules, test_followups_route_to_page, test_tools_end_to_end,
             test_copy_retry_and_abort, test_not_connected, test_firefox_wont_focus]
    failed = 0
    for t in tests:
        try:
            t()
            log(f"ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            log(f"BAD {t.__name__}: {e!r}")
    log("page_reader " + ("all passed" if not failed else f"{failed} tests failed"))
    raise SystemExit(failed != 0)
