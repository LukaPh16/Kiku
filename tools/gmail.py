"""gmail: check and read Gmail in Firefox through the browser bridge (no Gmail API). READ ONLY.

  "did Ana email me"     -> Gmail search box: visibly type from:ana, Enter -> read the result rows
                            (sender, subject, date, snippet, rects), highlighting each row while Kiku
                            says it -> rows kept in ctx.focus["emails"]
  "check my email"       -> the same for the inbox
  "read the second one" / "read the email from Ana"
                         -> icon to the row -> click -> extract the body: short -> read out,
                            long -> line scan while the LLM summarizes ("Ana is asking about ...")
  "go back"              -> from an open email back to the list
  "go to gmail"          -> open_website (config.KNOWN_SITES maps gmail to mail.google.com)

Gmail is never opened over the page you're on: Kiku switches to an existing Gmail tab (any window),
else opens a new tab (Ctrl+T) and types mail.google.com there.

Read only: Kiku clicks only the search box, list rows, and the Back / Inbox buttons, and types only
into the search box after checking it has focus (with Gmail's keyboard shortcuts on, a stray key
could archive or delete). It never sends, deletes, archives, or marks anything. Note: opening an
email marks it read in Gmail, as it would if you clicked it yourself.

Gmail's DOM changes often: every selector is in SEL below. role / aria-label first, Gmail class
names (.bog, .y2, .a3s) second, then text search; parse_row falls back to the row's text lines.
Pure helpers (parse_row, clean_body, pick_email, list_lines, ...) are tested in tests/test_gmail.py.
"""
import re
import time
from urllib.parse import unquote_plus, urlparse

import config
from browser import geometry
from browser.bridge import BridgeError
from control.actions import Aborted
from tools import close, go_back, open_website, page_reader
from tools.registry import Spoken, register, sorry

# ---- selectors (all of them) ----
# Lists are tried in order; the first with a visible match wins.
# Row fields are [selector inside the row, attribute or None for its text].
SEL = {
    "search_box": ['input[aria-label="Search mail"]', 'form[role=search] input[name=q]', 'input[name=q]',
                   '[role=search] input'],
    "search_box_text": "Search mail",             # text fallback: a textbox labelled like this
    "row": ['[role=main] tr[role=row]', 'tr[role=row]', '[role=main] tr.zA', 'tr.zA'],
    "row_fields": {
        "sender": [["span[email][name]", "name"], ["span[email]", None], ["[data-hovercard-id][name]", "name"]],
        "email": [["span[email]", "email"], ["[data-hovercard-id]", "data-hovercard-id"]],
        "subject": [["span.bog", None], ["span.bqe", None], [".y6 span", None]],
        "snippet": [["span.y2", None], [".y2", None]],
        "date": [["td.xW span[title]", None], ["td.xW span", None]],
        "date_full": [["td.xW span[title]", "title"], ["span[title][aria-label]", "title"]],
    },
    "no_results": ["No messages matched your search", "Your Primary tab is empty", "No new mail"],
    # body of the open email; the last visible match is the newest expanded message
    "body": ['[role=main] [role=listitem] div.a3s', '[role=main] div.a3s', 'div.a3s',
             '[role=main] [role=listitem]'],
    "back_button": ['[role=button][aria-label^="Back to"]', '[data-tooltip^="Back to"]'],
    "back_text": "Back to",                       # text fallback: a button labelled like this
    "inbox_link": ['[role=navigation] a[href$="#inbox"]', 'a[href$="#inbox"]'],
    "inbox_text": "Inbox",
}

_shell = _actions = _bridge = _llm = _page = _say = None
_listed = {"contact": None}   # what the last list was (to search again if the rows went away)


def _log(msg):
    print(f"[KIKU] gmail: {msg}", flush=True)


def init(shell, actions, bridge, llm, say=None):
    """say(text) speaks and blocks until done; without it the reply is returned as plain text."""
    global _shell, _actions, _bridge, _llm, _page, _say
    from browser.page import Page
    _shell, _actions, _bridge, _llm, _say = shell, actions, bridge, llm, say
    _page = Page(bridge, shell, actions)


class _Fail(Exception):
    """Stop the tool and say this."""


# ---- pure helpers ----

_DATE = re.compile(r"^(?:\d{1,2}:\d{2}\s?(?:[ap]\.?m\.?)?|[a-z]{3,9}\.? \d{1,2}|\d{1,2} [a-z]{3,9}\.?|"
                   r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}|yesterday|today)$", re.I)


def is_gmail(url):
    return (urlparse(url or "").hostname or "") == config.GMAIL_HOST


def is_open(url):
    """True if the URL shows one email (thread id at the end: #inbox/FMfcgz..., #search/x/FMfcgz...)."""
    frag = (url or "").partition("#")[2]
    last = frag.rsplit("/", 1)[-1] if "/" in frag else ""
    return (len(last) >= 16 and re.fullmatch(r"[A-Za-z0-9_-]+", last) is not None
            and re.search(r"[a-z]", last) is not None and re.search(r"[A-Z]", last) is not None)


def search_query(contact):
    c = " ".join(contact.split())
    return f"from:({c})" if " " in c else f"from:{c}"


def _squash(s):
    return re.sub(r"[\s()\"'+]+", "", s.lower())


def in_search(url, query):
    """True once Gmail's URL shows a search for query (#search/from%3Aana)."""
    frag = unquote_plus((url or "").partition("#")[2])
    return frag.lower().startswith("search/") and _squash(query) in _squash(frag)


def parse_row(r):
    """get_rows row -> email dict. Fields from SEL; missing ones from the row's text lines
    (sender first, date last, then subject - snippet)."""
    f = r.get("fields") or {}
    sender, subject, snippet, date = (f.get(k) or "" for k in ("sender", "subject", "snippet", "date"))
    lines = [ln for ln in r.get("lines") or [] if ln not in ("-", "–")]
    if lines and not (sender and subject):
        rest = list(lines)
        d = rest.pop() if len(rest) > 1 and _DATE.match(rest[-1]) else ""
        s = rest.pop(0) if rest else ""
        mid = " ".join(rest)
        if " - " in mid:
            subj, _, snip = mid.partition(" - ")
        else:
            subj, snip = (rest[0], " ".join(rest[1:])) if rest else ("", "")
        sender, subject = sender or s, subject or subj
        snippet, date = snippet or snip, date or d
    ft = r.get("field_targets") or {}
    return {"id": r.get("id"), "sender": sender.strip(), "email": f.get("email") or "",
            "subject": subject.strip(), "snippet": re.sub(r"^[-–]\s*", "", snippet.strip()),
            "date": date.strip(), "date_full": f.get("date_full") or date.strip(),
            "in_view": r.get("in_view", True), "target": r.get("target"),
            "click_target": ft.get("subject") or r.get("target")}


def same_email(a, b):
    return (a["sender"].lower(), a["subject"].lower(), a["date"]) == (b["sender"].lower(), b["subject"].lower(),
                                                                      b["date"])


def pick_email(emails, n=None, sender=None):
    """nth email (1-based, -1 = last) of the list, or the newest from sender. None if no match
    (or no n/sender and more than one listed)."""
    if sender:
        s = sender.lower().strip()
        emails = [e for e in emails if s in e["sender"].lower() or s in e["email"].lower()
                  or set(s.split()) & set(e["sender"].lower().split())]
        if n is None:
            return emails[0] if emails else None
    if n is None:
        return emails[0] if len(emails) == 1 else None
    return page_reader.nth(emails, n)


_QUOTE = [re.compile(p, re.M) for p in (
    r"^On [^\n]{4,200}?(?:\n[^\n]{0,120}?)?wrote:[ \t]*$",   # reply header (Gmail, Apple Mail)
    r"^-{2,} ?Original Message ?-{2,}",                     # Outlook
    r"^From: [^\n]+\n(?:Sent|Date): ",                      # Outlook
    r"^-- ?$",                                              # signature
    r"^>",                                                  # quoted lines
)]


def clean_body(text):
    """Email body for speech: no quoted replies or signature, links as "a link", lines kept."""
    cut = len(text)
    for p in _QUOTE:
        m = p.search(text)
        if m and m.start() > 0:
            cut = min(cut, m.start())
    text = re.sub(r"https?://\S+", "a link", text[:cut])
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def spoken(text):
    """Lines -> sentences (TTS pauses at a period, not at a line break)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return " ".join(ln if ln[-1] in ".!?,:;" else ln + "." for ln in lines)


def first_words(text, n):
    words = text.split()
    return " ".join(words[:n]) + ("..." if len(words) > n else "")


MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June",
          "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
          "nov": "November", "dec": "December"}
_MONTH = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?", re.I)
_FULL_DATE = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})")


def full_months(text):
    """"Nov 3" -> "November 3", "3 Dec." -> "3 December" (TTS says "Nov." as "nov")."""
    return _MONTH.sub(lambda m: MONTHS[m.group(1).lower()], text)


def spoken_date(d, full=""):
    """Gmail's short date -> words: "4:12 PM" -> "at 4:12 PM", "Nov 3" -> "on November 3",
    "11/3/25" -> "on November 3, 2025" (from the full date, so day/month order can't be misread)."""
    if not d:
        return ""
    if re.search(r"\d:\d", d):
        return f"at {d}"
    if d.lower() in ("today", "yesterday"):
        return d.lower()
    m = _FULL_DATE.search(full or "")
    if re.fullmatch(r"[\d/.-]+", d) and m:
        d = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
    return f"on {full_months(d)}"


_ORD = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"]


def list_lines(emails, contact=None, max_n=None):
    """(intro, [one sentence per row read out]). Row i of the sentences is emails[i]."""
    max_n = max_n or config.GMAIL_LIST_MAX
    n = len(emails)
    name = " ".join(w.capitalize() for w in contact.split()) if contact else ""
    if n == 0:
        return (f"I don't see any emails from {name}." if contact else "I don't see any emails."), []
    one_sender = len({e["sender"].lower() for e in emails}) == 1 and emails[0]["sender"]
    count = "One email" if n == 1 else (f"{n} emails" if n < 50 else "At least 50 emails")
    if contact:
        intro = f"{count} from {emails[0]['sender'] if one_sender else name}"
        if n > max_n:
            intro += f". The newest {max_n}"
    else:
        intro = "Your newest email" if n == 1 else f"Your newest {min(n, max_n)} emails"
    lines = []
    for i, e in enumerate(emails[:max_n]):
        what = ", ".join(x for x in (e["subject"] or "no subject", spoken_date(e["date"], e.get("date_full")))
                         if x)
        who = "" if (contact and one_sender) else f"from {e['sender'] or 'an unknown sender'}: "
        lead = "" if n == 1 else f"{_ORD[i] if i < len(_ORD) else f'Number {i + 1}'}, "
        s = f"{lead}{who}{what}."
        lines.append(s[0].upper() + s[1:])
    return intro + ":", lines


# ---- browser ----

def _ready():
    """None if Gmail can be driven, else what to say. Brings Firefox to the front."""
    if _page is None:
        return "Gmail isn't set up."
    _actions.begin()
    try:
        open_website.focus_firefox()
    except RuntimeError as e:
        return sorry(e, "I can't bring Firefox up")
    if not _bridge.connected and not _bridge.wait_connected(config.PAGE_CONNECT_WAIT_S):
        return "The Firefox extension isn't connected, so I can't see Gmail."
    return None


def _url():
    return _bridge.call("get_url", timeout=2)["url"]


def _wait_url(ok, seconds):
    end = time.monotonic() + seconds
    while True:
        if ok(_url()):
            return True
        if time.monotonic() > end:
            return False
        _actions.wait(0.15)


def _find(sels, text=None, role=None):
    """First visible element for the selectors, else by its label text (within role)."""
    for s in sels:
        try:
            els = _page.find(selector=s, limit=50)
        except BridgeError as e:
            _log(f"selector {s!r}: {e}")
            continue
        if els:
            return els
    if text:
        try:
            els = _page.find(role=role, text=text, limit=5)
        except BridgeError as e:
            _log(f"text {text!r}: {e}")
            return []
        if els:
            _log(f"found {text!r} by text (selectors in SEL may need an update)")
        return els
    return []


def _rows():
    return [parse_row(r) for r in _page.rows(SEL["row"], SEL["row_fields"])]


def _no_results():
    return any(_find([], t) for t in SEL["no_results"])


def _wait_rows(url_ok, before_url):
    """Rows once the URL is right and the rows stopped changing; [] if Gmail says there are none."""
    t0 = time.monotonic()
    last = None
    while True:
        url = _url()
        if url_ok(url) or (url != before_url and time.monotonic() - t0 > 2):
            rows = _rows()
            if rows:
                sig = [r["id"] for r in rows]
                if sig == last:
                    return rows
                last = sig
            elif _no_results():
                return []
        if time.monotonic() - t0 > config.GMAIL_RESULTS_WAIT_S:
            raise _Fail("Gmail didn't show the emails in time.")
        _actions.wait(0.2)


def _switch_to_gmail_tab():
    """Bring up an open Gmail tab (in any Firefox window). False if there is none."""
    tabs = _bridge.call("find_tabs", host=config.GMAIL_HOST)
    if not tabs:
        return False
    tab = max(tabs, key=lambda t: t["active"])   # one already showing in its window first
    info = _bridge.call("activate_tab", tab_id=tab["tab_id"])
    title = info.get("title") or ""
    win = next((w for w in _shell.list_windows()
                if geometry.is_firefox(w) and title and (w.get("title") or "").startswith(title)), None)
    if win and not win["focused"]:
        f = win["frame"]
        _actions.point_at((f["x"] + f["width"] / 2, f["y"] + 20))
        _shell.activate_window(win["id"])
        end = time.monotonic() + 1.5
        while not next((w["focused"] for w in _shell.list_windows() if w["id"] == win["id"]), True):
            if time.monotonic() > end:
                _log("Gmail window didn't take focus")
                break
            _actions.wait(0.05)
    _log(f"switched to the Gmail tab {title!r}")
    return True


def _ensure_gmail(ctx):
    """Gmail in the active tab with its search box ready. Returns the search box element.
    Never navigates the page you're on: switches to a Gmail tab, else open_website opens a new tab."""
    if not is_gmail(_url()) and not _switch_to_gmail_tab():
        _log("no Gmail tab; opening one in a new tab")
        open_website.open_website(ctx, "gmail")
        _actions.check()   # open_website turns a panic stop into "Stopped."
    end = time.monotonic() + config.GMAIL_READY_WAIT_S
    while True:
        url = _url()
        if "accounts.google.com" in url:
            raise _Fail("Gmail wants you to sign in first.")
        box = _find(SEL["search_box"], SEL["search_box_text"], "textbox") if is_gmail(url) else []
        if box:
            return box[0]
        if time.monotonic() > end:
            raise _Fail("I can't find Gmail's search box.")
        _actions.wait(0.3)


def _box_focused(box, seconds=1.0):
    """True if the page has keyboard focus and it's in the search box."""
    end = time.monotonic() + seconds
    while True:
        f = _page.focused()
        el = f.get("element")
        ok = bool(f.get("has_focus") and el and (
            el["id"] == box["id"] or (el["tag"] in ("input", "textarea") and "search" in el["text"].lower())))
        if ok:
            return True
        if time.monotonic() > end:
            _log(f"search box doesn't have focus: page focused={f.get('has_focus')}, "
                 f"focus on {el and (el['tag'], el['text'][:40])}")
            return False
        _actions.wait(0.1)


def _search(box, query):
    """Type query into the search box (visibly) and press Enter. Returns the result rows."""
    t = box["target"]
    before = _url()
    _shell.highlight_rect(*t.rect)
    _actions.click(t)   # real click: focuses the page content and the box
    if not _box_focused(box):
        raise _Fail("I couldn't get into Gmail's search box, so I didn't type anything.")
    _actions.key("ctrl+a", target=t)   # replace an earlier search
    _actions.type_text(query, target=t)
    if not _box_focused(box, 0.3):
        raise _Fail("Gmail's search box lost focus, so I stopped typing.")
    _actions.key("enter", target=t)
    _shell.clear_highlight()
    return _wait_rows(lambda u: in_search(u, query), before)


def _inbox():
    url = _url()
    if url.partition("#")[2] == "inbox":
        return _wait_rows(lambda u: True, url)
    link = _find(SEL["inbox_link"], SEL["inbox_text"], "link")
    if link:
        _page.click(link[0])
    else:
        _bridge.call("navigate", url=url.partition("#")[0] + "#inbox")
    return _wait_rows(lambda u: u.partition("#")[2] == "inbox", url)


def _list(ctx, contact):
    """Search (or show the inbox) and remember the rows. Returns the email dicts."""
    box = _ensure_gmail(ctx)
    emails = _search(box, search_query(contact)) if contact else _inbox()
    _listed["contact"] = contact
    ctx.focus.update(app="Firefox", page=_url(), emails=emails, email=None)
    _log(f"{len(emails)} rows" + (f" from {contact!r}" if contact else " in the inbox"))
    return emails


def _point_row(e):
    """Highlight a row and move Kiku's icon to its left end."""
    t = e["target"]
    if not e.get("in_view", True):
        t = _page.scroll_into_view({"id": e["id"], "text": e["subject"]})["target"]
    _shell.highlight_rect(*t.rect)
    _actions.point_at((t.x + 20, t.y + t.h / 2), hover_ms=0)


def _speak(text):
    _actions.check()
    _say(text)
    _actions.check()


def _find_row(e):
    return next((r for r in _rows() if same_email(r, e)), None)


def _open_row(row):
    """Icon to the row's subject, click, wait until the email is open."""
    if not row.get("in_view", True):
        el = _page.scroll_into_view({"id": row["id"], "text": row["subject"]})
        row = dict(row, target=el["target"], click_target=el["target"])
    t = row["click_target"]
    _shell.highlight_rect(*row["target"].rect)
    _actions.point_at(t)
    _shell.click_pulse()
    _bridge.call("click", element_id=row["id"])   # DOM click first: no hover buttons pop up
    if not _wait_url(is_open, 1.5):
        _log("DOM click didn't open it; clicking the subject")
        _actions.click(t)
        if not _wait_url(is_open, config.GMAIL_OPEN_WAIT_S):
            raise _Fail("Gmail didn't open that email.")
    _shell.clear_highlight()


def _wait_body():
    end = time.monotonic() + config.GMAIL_OPEN_WAIT_S
    while True:
        els = _find(SEL["body"])
        if els:
            return els[-1]
        if time.monotonic() > end:
            raise _Fail("I can't find the text of that email.")
        _actions.wait(0.2)


def _back_to_list():
    before = _url()
    btn = _find(SEL["back_button"], SEL["back_text"], "button")
    if btn:
        _page.click(btn[0])
    if not btn or not _wait_url(lambda u: not is_open(u), 1.5):
        _log("Back button missing or didn't work; using browser back")
        _bridge.call("go_back")
        if not _wait_url(lambda u: not is_open(u), 3):
            raise _Fail("I couldn't get back to the list.")
    return _wait_rows(lambda u: True, before)


# ---- reading one email ----

SUMMARY_SYSTEM = ("You are Kiku, telling the user out loud what an email says. In two short sentences, in "
                  "your own words, say what the sender wants or tells the user. Start with the sender's first "
                  "name, like 'Ana is asking about ...'. Never quote the email. No lists, markdown, or emoji.")


def _show_body(body):
    """Scroll the body into view, highlight its visible part, icon to its start."""
    body = _page.scroll_into_view(body, block="start")
    t = body["target"]
    rect = body["viewport"].clip(t.rect) or t.rect
    _shell.highlight_rect(*rect)
    _actions.point_at((t.x + 20, t.y + 14), hover_ms=0)
    return body


def _read(ctx, e):
    if not is_gmail(_url()):
        _log("Gmail isn't the active tab; switching to it (or opening it in a new tab)")
        _ensure_gmail(ctx)
    if is_open(_url()):
        _back_to_list()
    url = _url()
    row = _find_row(e)
    contact = _listed["contact"] or e.get("email") or e.get("sender")
    if row is None and contact:
        _log(f"row not on screen; searching for {contact!r}")
        _search(_ensure_gmail(ctx), search_query(contact))
        url = _url()
        row = _find_row(e)
    if row is None:
        raise _Fail("I can't find that email on the screen anymore.")
    _open_row(row)
    body = _wait_body()
    text = clean_body(_page.text(body, inner=True))
    ctx.focus.update(email=e, page=_url())
    words = len(text.split())
    _log(f"opened {e['sender']!r} {e['subject']!r}: {words} words")
    if not text:
        return "That email has no text I can read."
    head = f"From {e['sender'] or 'someone'}, {e['subject'] or 'no subject'}."
    if words > config.GMAIL_READ_ALOUD_WORDS and _llm is not None and _llm.available \
            and page_reader._page is not None:
        first = (e["sender"] or "The sender").split()[0]
        prompt = (f"From: {e['sender']} <{e['email']}>\nSubject: {e['subject']}\nDate: {e['date_full']}\n\n"
                  f"Email:\n{page_reader.truncate(spoken(text), config.PAGE_CONTEXT_TOKENS)}\n\n"
                  f"Tell me what {first} wants or says, starting with '{first}'.")
        a = page_reader.explain(ctx, url, body, SUMMARY_SYSTEM, prompt, text)
        if _say is None or a == "Stopped." or a.startswith("Sorry"):
            return a
        _show_body(body)   # keep the email outlined while Kiku says what it's about
        _speak(a)
        return Spoken(a)
    say = f"{head} {spoken(text)}" if words <= config.GMAIL_READ_ALOUD_WORDS else \
        f"{head} It's long, here's how it starts: {first_words(spoken(text), config.GMAIL_READ_ALOUD_WORDS)}"
    _show_body(body)
    if _say is None:
        return say
    _speak(say)
    return Spoken(say)


# ---- tools ----

def _guard(fn):
    """Run fn() -> reply; turns stops and failures into something to say."""
    try:
        return fn()
    except Aborted:
        _log("aborted")
        return "Stopped."
    except _Fail as e:
        _log(f"stopped: {e}")
        return str(e)
    except BridgeError as e:
        _log(f"bridge: {e}")
        return "Sorry, I lost track of Gmail."
    finally:
        if _shell is not None:
            _shell.clear_highlight()


@register("check_email", "Check the inbox, optionally for emails from one contact",
          {"contact": {"type": "string", "required": False}})
def check_email(ctx, contact=None):
    def run():
        msg = _ready()
        if msg:
            return msg
        emails = _list(ctx, contact)
        intro, lines = list_lines(emails, contact)
        text = " ".join([intro, *lines]) if lines else intro
        if _say is None:   # no voice to pace it: show each row briefly, then return the text
            for e in emails[:len(lines)]:
                _point_row(e)
                _actions.wait(config.GMAIL_ROW_PAUSE_S)
            return text
        _speak(intro if lines else text)
        for e, line in zip(emails, lines):
            _point_row(e)
            _speak(line)
        return Spoken(text)
    return _guard(run)


@register("read_email", "Read an email from the last email list: the nth (1 = newest, -1 = last) "
                        "or the newest from a sender",
          {"n": {"type": "integer", "required": False}, "sender": {"type": "string", "required": False}})
def read_email(ctx, n=None, sender=None):
    def run():
        emails = ctx.focus.get("emails")
        if not emails and not sender:
            return "I haven't listed any emails yet. Ask me if someone emailed you."
        msg = _ready()
        if msg:
            return msg
        if not emails or (sender and pick_email(emails, None, sender) is None):
            emails = _list(ctx, sender)   # "read the email from Ana" with no list for Ana: search first
        e = pick_email(emails, n, sender)
        if e is None:
            if not emails:
                return f"I don't see any emails from {sender}." if sender else "I don't see any emails."
            if n is not None:
                return f"I only listed {len(emails)} email{'s' if len(emails) != 1 else ''}."
            return "Which one? Say the first, the second, and so on."
        return _read(ctx, e)
    return _guard(run)


@register("back", "Go back (from an open email to the list, or the previous page)")
def back(ctx):
    def run():
        try:
            front = close.front_window(_shell.list_windows()) if _shell is not None else None
            gmail_open = (_page is not None and _bridge.connected and front is not None
                          and geometry.is_firefox(front) and is_open(_url()) and is_gmail(_url()))
        except BridgeError:
            gmail_open = False
        if not gmail_open:
            return go_back.go_back(ctx)   # any other page or app
        msg = _ready()
        if msg:
            return msg
        _back_to_list()
        ctx.focus.update(email=None, page=_url())
        return "Back to the list."
    return _guard(run)
