"""Gmail tool tests without Firefox, Gmail or Ollama: row parsing (selectors and text fallback),
body cleanup, picking, what Kiku says, router rules, and the tools end to end against a fake Gmail.
Every flow also checks that Kiku stayed read only (what it clicked, typed and pressed).

  venv/bin/python -m tests.test_gmail
"""
import threading

from brain.context import Context
from brain.router import match_rules
from tools import gmail, page_reader
from tools.registry import Spoken

BASE = "https://mail.google.com/mail/u/0/"
THREAD = "FMfcgzQXJkLmNpQrStUvWx"


def log(msg):
    print(f"[KIKU] test: {msg}", flush=True)


# ---- pure helpers ----

def test_urls_and_query():
    assert gmail.is_gmail(BASE + "#inbox") and not gmail.is_gmail("https://accounts.google.com/x")
    assert gmail.is_open(BASE + "#inbox/" + THREAD) and gmail.is_open(BASE + "#search/from%3Aana/" + THREAD)
    assert not gmail.is_open(BASE + "#inbox") and not gmail.is_open(BASE + "#search/from%3Aana")
    assert not gmail.is_open(BASE + "#search/anastasiawilliamson")      # all lowercase: a query, not an id
    assert gmail.search_query("ana") == "from:ana"
    assert gmail.search_query("john  smith") == "from:(john smith)"
    assert gmail.in_search(BASE + "#search/from%3Aana", "from:ana")
    assert gmail.in_search(BASE + "#search/from%3A(john+smith)", "from:(john smith)")
    assert not gmail.in_search(BASE + "#search/from%3Abob", "from:ana")
    assert not gmail.in_search(BASE + "#inbox", "from:ana")


def test_parse_row():
    r = {"id": "k1", "fields": {"sender": "Ana Kowalski", "email": "ana@x.com", "subject": "Lunch on Friday",
                                "snippet": " - Are we still on?", "date": "Nov 3",
                                "date_full": "Tue, Nov 3, 2026, 4:12 PM"}, "lines": []}
    e = gmail.parse_row(r)
    assert (e["sender"], e["subject"], e["snippet"], e["date"]) == ("Ana Kowalski", "Lunch on Friday",
                                                                    "Are we still on?", "Nov 3"), e
    # Gmail renamed its classes: only the row text is left
    e = gmail.parse_row({"id": "k2", "fields": {}, "lines": ["Ana Kowalski", "Lunch on Friday", "-",
                                                              "Are we still on?", "4:12 PM"]})
    assert (e["sender"], e["subject"], e["snippet"], e["date"]) == ("Ana Kowalski", "Lunch on Friday",
                                                                    "Are we still on?", "4:12 PM"), e
    e = gmail.parse_row({"id": "k3", "fields": {}, "lines": ["Bob", "Invoice - due soon", "11/3/25"]})
    assert (e["sender"], e["subject"], e["snippet"], e["date"]) == ("Bob", "Invoice", "due soon", "11/3/25"), e


BODY_LONG_QUOTE = """Hi Luka,

Are we still on for lunch on Friday? See https://maps.example.com/place?id=1

Ana
--
Ana Kowalski | Acme

On Mon, Nov 2, 2026 at 9:00 AM Luka <luka@x.com>
wrote:
> Sure, Friday works.
"""


def test_clean_body():
    t = gmail.clean_body(BODY_LONG_QUOTE)
    assert t == "Hi Luka,\nAre we still on for lunch on Friday? See a link\nAna", repr(t)
    assert gmail.spoken(t) == "Hi Luka, Are we still on for lunch on Friday? See a link. Ana."
    assert gmail.clean_body("> only a quote") == "> only a quote"   # nothing before it: keep it
    assert gmail.first_words("a b c d", 2) == "a b..."


EMAILS = [
    {"sender": "Ana Kowalski", "email": "ana@x.com", "subject": "Lunch on Friday", "date": "4:12 PM"},
    {"sender": "Ana Kowalski", "email": "ana@x.com", "subject": "Photos", "date": "Nov 1"},
    {"sender": "Bob Stone", "email": "bob@y.com", "subject": "", "date": "Oct 30"},
]


def test_pick_email():
    assert gmail.pick_email(EMAILS, 2) is EMAILS[1]
    assert gmail.pick_email(EMAILS, -1) is EMAILS[2]
    assert gmail.pick_email(EMAILS, 7) is None
    assert gmail.pick_email(EMAILS, None, "bob") is EMAILS[2]
    assert gmail.pick_email(EMAILS, None, "ana kowalski") is EMAILS[0]
    assert gmail.pick_email(EMAILS, 2, "ana") is EMAILS[1]
    assert gmail.pick_email(EMAILS, None, "carol") is None
    assert gmail.pick_email(EMAILS, None) is None             # which one?
    assert gmail.pick_email(EMAILS[:1], None) is EMAILS[0]


def test_dates():
    assert gmail.spoken_date("Nov 3") == "on November 3"
    assert gmail.spoken_date("3 Dec.") == "on 3 December"
    assert gmail.spoken_date("Sept 9") == "on September 9" and gmail.spoken_date("May 2") == "on May 2"
    assert gmail.spoken_date("11/3/25", "Mon, Nov 3, 2025, 4:12 PM") == "on November 3, 2025"
    assert gmail.spoken_date("4:12 PM") == "at 4:12 PM" and gmail.spoken_date("") == ""
    assert gmail.full_months("Novel ideas on Mar 4") == "Novel ideas on March 4"   # whole words only


def test_list_lines():
    intro, lines = gmail.list_lines(EMAILS[:2], "ana")
    assert intro == "2 emails from Ana Kowalski:", intro
    assert lines == ["First, Lunch on Friday, at 4:12 PM.", "Second, Photos, on November 1."], lines
    intro, lines = gmail.list_lines(EMAILS[:1], "ana")
    assert (intro, lines) == ("One email from Ana Kowalski:", ["Lunch on Friday, at 4:12 PM."])
    intro, lines = gmail.list_lines(EMAILS, None)
    assert intro == "Your newest 3 emails:" and lines[2] == "Third, from Bob Stone: no subject, on October 30.", lines
    intro, lines = gmail.list_lines(EMAILS, "ana")             # from:ana also matched someone else
    assert intro == "3 emails from Ana:" and lines[0].startswith("First, from Ana Kowalski: "), (intro, lines)
    assert gmail.list_lines([], "john smith") == ("I don't see any emails from John Smith.", [])
    intro, _ = gmail.list_lines(EMAILS * 3, "ana", max_n=5)
    assert intro == "9 emails from Ana. The newest 5:", intro


ROUTES = [
    ("Did Ana email me?", ("check_email", {"contact": "ana"})),
    ("Check my email.", ("check_email", {})),
    ("Read the email from Ana.", ("read_email", {"sender": "ana"})),
    ("open Ana's email", ("read_email", {"sender": "ana"})),
    ("read the latest email from john smith", ("read_email", {"sender": "john smith"})),
    ("Read the second email.", ("read_email", {"n": 2})),
    ("Go back.", ("back", {})),
    ("go to gmail", ("open_website", {"site": "gmail"})),
]


def test_router_rules():
    ctx = Context()
    bad = []
    for phrase, want in ROUTES:
        r = match_rules(phrase, ctx)
        got = (r["tool"], r["args"]) if r else None
        if got != want:
            bad.append((phrase, got, want))
    assert not bad, bad
    assert match_rules("read the second one", ctx) is None      # nothing listed yet
    ctx.focus["emails"] = EMAILS
    assert match_rules("read the second one", ctx) == {"tool": "read_email", "args": {"n": 2}}


# ---- a fake Gmail ----

SHORT_BODY = "Hi Luka,\nAre we still on for lunch on Friday?\nAna\n\nOn Mon, Nov 2 Luka wrote:\n> yes"
LONG_BODY = "Hi Luka,\n" + " ".join(["We need to plan the budget review for next quarter."] * 30) + "\nAna"
VIEW = {"inner_width": 1000, "inner_height": 800, "dpr": 1}


def rect(y, h=36, x=0, w=1000):
    return {"x": x, "y": y, "width": w, "height": h}


class FakeGmail:
    """Bridge stand-in: Gmail with an inbox, a from:ana search, open emails and a Back button."""
    connected = True

    def __init__(self, url=BASE + "#inbox", fields_ok=True, focus_ok=True, dom_click_ok=True, box_sel_ok=True,
                 bodies=None):
        self.url, self.fields_ok, self.focus_ok = url, fields_ok, focus_ok
        self.dom_click_ok, self.box_sel_ok = dom_click_ok, box_sel_ok
        self.bodies = bodies or {"r1": SHORT_BODY, "r2": LONG_BODY}
        self.calls, self.focused, self.list_url = [], False, url
        self.typed = ""
        self.other_tabs = []   # find_tabs / activate_tab

    def wait_connected(self, timeout):
        return True

    # what the page shows
    def rows_now(self):
        if gmail.is_open(self.url):
            return []
        if "#search/" in self.url:
            if "ana" not in self.url:
                return []
            return [("r1", "Ana Kowalski", "Lunch on Friday", "Are we still on?", "4:12 PM"),
                    ("r2", "Ana Kowalski", "Budget", "We need to plan", "Nov 1")]
        return [("r9", "Bob Stone", "Invoice", "Due soon", "Oct 30")]

    def row_dict(self, i, row):
        rid, sender, subj, snip, date = row
        y = 100 + i * 40
        d = {"id": rid, "rect": rect(y), "in_view": True, "fields": {}, "field_rects": {},
             "lines": [sender, subj, "-", snip, date]}
        if self.fields_ok:
            d["fields"] = {"sender": sender, "email": f"{sender.split()[0].lower()}@x.com", "subject": subj,
                           "snippet": f" - {snip}", "date": date, "date_full": date}
            d["field_rects"] = {"subject": rect(y + 8, 20, 300, 200)}
        return d

    def el(self, eid, label, r, tag="div"):
        return {"id": eid, "tag": tag, "role": "", "text": label, "rect": r, "rects": [r], "in_view": True}

    def open_row(self, rid):
        self.list_url = self.url
        self.opened = rid
        self.url = self.url + "/" + THREAD

    def call(self, cmd, timeout=None, **a):
        self.calls.append((cmd, a))
        view = dict(VIEW, url=self.url)
        if cmd == "find_tabs":
            return [dict(t, active=False) for t in self.other_tabs if a["host"] in t["url"]]
        if cmd == "activate_tab":
            t = next(t for t in self.other_tabs if t["tab_id"] == a["tab_id"])
            self.url = t["url"]
            return t
        if cmd == "get_url":
            return {"url": self.url, "title": "Gmail", "load_seq": 1, "status": "complete", "tab_id": 1}
        if cmd == "get_rows":
            assert a["row"] == gmail.SEL["row"] and a["fields"] == gmail.SEL["row_fields"]
            return {"rows": [self.row_dict(i, r) for i, r in enumerate(self.rows_now())], "view": view}
        if cmd == "get_elements":
            return {"elements": self.elements(a), "view": view}
        if cmd == "get_focus":
            box = self.el("box", "Search mail", rect(10, 40, 200, 500), "input")
            return {"has_focus": self.focused, "element": box if self.focused else None, "view": view}
        if cmd == "click":
            eid = a["element_id"]
            if eid.startswith("r") and self.dom_click_ok:
                self.open_row(eid)
            elif eid == "back":
                self.url = self.list_url
            return {"clicked": eid}
        if cmd == "go_back":
            self.url = self.list_url
            return {}
        if cmd == "dom_text":
            assert a.get("inner") is True
            return {"text": self.bodies[self.opened]}
        if cmd == "scroll_into_view":
            return {"rect": rect(200, 600), "in_view": True, "view": view}
        if cmd == "line_ranges":
            return {"lines": [{"start": 0, "end": 30, "rect": rect(200, 20)}], "view": view}
        if cmd == "select_range":
            return {"text": "x", "rects": [], "view": view}
        if cmd in ("clear_selection", "get_viewport"):
            return {"view": view}
        raise AssertionError(f"unexpected bridge command {cmd} {a}")

    def elements(self, a):
        sel, text, role = a.get("selector"), a.get("text"), a.get("role")
        is_open = gmail.is_open(self.url)
        if sel == gmail.SEL["search_box"][0] and self.box_sel_ok:
            return [self.el("box", "Search mail", rect(10, 40, 200, 500), "input")]
        if role == "textbox" and text == "Search mail":
            return [self.el("box", "Search mail", rect(10, 40, 200, 500), "input")]
        if sel == gmail.SEL["body"][0] and is_open:
            return [self.el("body", "", rect(200, 600, 100, 800))]
        if sel == gmail.SEL["back_button"][0] and is_open:
            return [self.el("back", "Back to Search results", rect(60, 30, 100, 30))]
        if text == gmail.SEL["no_results"][0] and "#search/" in self.url and not self.rows_now() and not is_open:
            return [self.el("nores", text, rect(120, 30))]
        return []


class FakeShell:
    def __init__(self):
        self.calls = []

    def list_windows(self):
        return [{"id": 1, "app_id": "firefox", "title": "Gmail — Mozilla Firefox", "focused": True, "minimized": False, "on_active_workspace": True,
                 "monitor": 0, "frame": {"x": 0, "y": 0, "width": 1000, "height": 900}}]

    def get_monitors(self):
        return [{"index": 0, "scale": 1}]

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


class FakeActions:
    """Real input goes to the fake page: clicking the search box focuses it (if focus_ok), clicking a
    row's subject opens it, typing only lands in a focused box."""
    aborted = False

    def __init__(self, page):
        self.page, self.log = page, []
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
        self.check()

    def click(self, target, button=1):
        self.check()
        x, y = target.center
        y -= 100   # global -> page px: the fake window has 100 px of toolbars above the content
        self.log.append(("click", round(y)))
        if y < 60:   # the search box
            self.page.focused = self.page.focus_ok
            return
        for i, row in enumerate(self.page.rows_now()):
            if 100 + i * 40 <= y < 136 + i * 40:
                self.page.open_row(row[0])

    def key(self, combo, target=None):
        self.check()
        self.log.append(("key", combo))
        if combo == "enter":
            self.page.url = BASE + "#search/" + self.page.typed.replace(":", "%3A").replace(" ", "+")
            self.page.focused = False
        elif combo == "ctrl+a":
            self.page.typed = ""

    def type_text(self, text, target=None, cps=None):
        self.check()
        self.log.append(("type", text))
        assert self.page.focused, "typed while the search box didn't have focus"
        self.page.typed += text


class FakeLlm:
    available = True

    def __init__(self):
        self.prompts = []

    def ask(self, system, prompt, history=(), **kw):
        self.prompts.append((system, prompt))
        threading.Event().wait(0.05)   # "thinking" long enough for the line scan to run
        return "Ana is asking to plan the budget review."


def setup(say=True, **kw):
    import config
    config.SCAN_LINE_MS = 5
    page, shell = FakeGmail(**kw), FakeShell()
    actions, llm, said = FakeActions(page), FakeLlm(), []
    gmail.open_website.focus_firefox = lambda: None
    gmail.init(shell, actions, page, llm, say=said.append if say else None)
    page_reader.init(shell, actions, page, llm)
    return page, shell, actions, llm, said


def assert_read_only(page, actions):
    """Kiku clicked only rows / Back / the search box, pressed only ctrl+a and Enter, typed only from:..."""
    clicked = [a["element_id"] for c, a in page.calls if c == "click"]
    assert all(e.startswith("r") or e == "back" for e in clicked), clicked
    assert not [c for c, _ in page.calls if c == "navigate"]
    keys = [x for k, x in actions.log if k == "key"]
    assert set(keys) <= {"ctrl+a", "enter"}, keys
    assert all(x.startswith("from:") for k, x in actions.log if k == "type"), actions.log


# ---- end to end ----

def test_check_email_from_contact():
    page, shell, actions, llm, said = setup()
    ctx = Context()
    reply = gmail.check_email(ctx, "ana")
    assert isinstance(reply, Spoken), reply
    assert said == ["2 emails from Ana Kowalski:", "First, Lunch on Friday, at 4:12 PM.",
                    "Second, Budget, on November 1."], said
    assert [x for k, x in actions.log if k in ("key", "type")] == ["ctrl+a", "from:ana", "enter"], actions.log
    assert [e["subject"] for e in ctx.focus["emails"]] == ["Lunch on Friday", "Budget"]
    hl = [a for n, a in shell.calls if n == "highlight_rect"]
    assert (0, 200, 1000, 36) in hl and (0, 240, 1000, 36) in hl, hl   # each row highlighted as it's said
    assert shell.calls[-1][0] == "clear_highlight"
    assert_read_only(page, actions)


def test_check_email_without_voice_and_broken_selectors():
    page, shell, actions, llm, said = setup(say=False, fields_ok=False, box_sel_ok=False)
    ctx = Context()
    reply = gmail.check_email(ctx, "ana")
    assert not isinstance(reply, Spoken) and reply.startswith("2 emails from Ana Kowalski: First, Lunch on Friday")
    assert ctx.focus["emails"][1]["snippet"] == "We need to plan"            # parsed from the row text
    assert_read_only(page, actions)


def test_no_results_and_inbox():
    page, shell, actions, llm, said = setup()
    ctx = Context()
    assert gmail.check_email(ctx, "carol") == "I don't see any emails from Carol."
    assert said == ["I don't see any emails from Carol."] and ctx.focus["emails"] == []
    page.url = BASE + "#inbox"
    said.clear()
    gmail.check_email(ctx, None)
    assert said == ["Your newest email:", "From Bob Stone: Invoice, on October 30."], said


def test_focus_guard():
    page, shell, actions, llm, said = setup(focus_ok=False)
    reply = gmail.check_email(Context(), "ana")
    assert "didn't type anything" in reply, reply
    assert not [k for k, _ in actions.log if k in ("key", "type")], actions.log


def test_read_short_and_back():
    page, shell, actions, llm, said = setup()
    ctx = Context()
    gmail.check_email(ctx, "ana")
    said.clear()
    reply = gmail.read_email(ctx, n=1)
    assert isinstance(reply, Spoken) and said == [
        "From Ana Kowalski, Lunch on Friday. Hi Luka, Are we still on for lunch on Friday? Ana."], said
    assert ("click", {"element_id": "r1"}) in page.calls and ctx.focus["email"]["subject"] == "Lunch on Friday"
    assert gmail.back(ctx) == "Back to the list."
    assert not gmail.is_open(page.url) and ctx.focus["email"] is None
    # read the second one while the first is still open: back to the list first, then open it
    gmail.read_email(ctx, n=1)
    assert gmail.read_email(ctx, n=2) == "Ana is asking to plan the budget review."
    assert ("click", {"element_id": "back"}) in page.calls
    assert_read_only(page, actions)


def test_read_long_summarizes():
    page, shell, actions, llm, said = setup()
    ctx = Context()
    gmail.check_email(ctx, "ana")
    said.clear()
    reply = gmail.read_email(ctx, n=2)
    assert reply == "Ana is asking to plan the budget review." and isinstance(reply, Spoken)
    assert "Subject: Budget" in llm.prompts[0][1] and "starting with 'Ana'" in llm.prompts[0][1]
    assert ("show_caption", (reply,)) in shell.calls and said == [reply]
    # the email stays outlined while the summary is said: highlight after the scan's clear, then clear at the end
    names = [n for n, _ in shell.calls]
    last_hl = len(names) - 1 - names[::-1].index("highlight_rect")
    assert "clear_highlight" in names[:last_hl] and names[-1] == "clear_highlight", names[-8:]
    assert any(c == "select_range" for c, _ in page.calls)                  # scanned while thinking
    assert_read_only(page, actions)


def test_read_by_sender_without_list_and_real_click():
    page, shell, actions, llm, said = setup(dom_click_ok=False)
    ctx = Context()
    reply = gmail.read_email(ctx, sender="ana")   # nothing listed yet: searches, then opens the newest
    assert reply.startswith("From Ana Kowalski, Lunch on Friday."), reply
    assert ("click", 118) in actions.log, actions.log   # DOM click didn't work: real click on the subject
    assert gmail.read_email(Context(), n=1) == "I haven't listed any emails yet. Ask me if someone emailed you."
    gmail.back(ctx)
    assert gmail.read_email(ctx, n=5) == "I only listed 2 emails."
    assert_read_only(page, actions)


def test_gmail_tab_never_replaces_the_page():
    # Gmail open in another tab: switch to it, don't type anything into the address bar
    page, shell, actions, llm, said = setup(url="https://meet.google.com/abc-defg-hij")
    page.other_tabs = [{"tab_id": 7, "url": BASE + "#inbox", "title": "Gmail", "status": "complete", "load_seq": 1}]
    opened, real_open = [], gmail.open_website.open_website
    gmail.open_website.open_website = lambda ctx, site: opened.append(site)
    gmail.check_email(Context(), "ana")
    assert ("activate_tab", {"tab_id": 7}) in page.calls and opened == []
    assert [x for k, x in actions.log if k == "type"] == ["from:ana"], actions.log
    # no Gmail tab anywhere: open_website (which opens a new tab) is asked for gmail
    page, shell, actions, llm, said = setup(url="https://meet.google.com/abc-defg-hij")

    def fake_open(ctx, site):
        opened.append(site)
        page.url = BASE + "#inbox"
    gmail.open_website.open_website = fake_open
    gmail.check_email(Context(), "ana")
    gmail.open_website.open_website = real_open
    assert opened == ["gmail"], opened


def test_abort_while_speaking():
    page, shell, actions, llm, said = setup()

    def say(text):
        said.append(text)
        actions._abort.set()   # panic hotkey while Kiku reads the list
    gmail._say = say
    assert gmail.check_email(Context(), "ana") == "Stopped."
    assert len(said) == 1 and shell.calls[-1][0] == "clear_highlight"


if __name__ == "__main__":
    tests = [test_urls_and_query, test_parse_row, test_clean_body, test_pick_email, test_dates, test_list_lines,
             test_router_rules, test_check_email_from_contact, test_check_email_without_voice_and_broken_selectors,
             test_no_results_and_inbox, test_focus_guard, test_read_short_and_back, test_read_long_summarizes,
             test_read_by_sender_without_list_and_real_click, test_gmail_tab_never_replaces_the_page,
             test_abort_while_speaking]
    failed = 0
    for t in tests:
        try:
            t()
            log(f"ok  {t.__name__}")
        except Exception as e:
            failed += 1
            log(f"BAD {t.__name__}: {e!r}")
    log("gmail " + ("all passed" if not failed else f"{failed} tests failed"))
    raise SystemExit(failed != 0)
