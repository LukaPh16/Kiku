"""Router: fast-path regex rules first, the LLM only when no rule matches.

match_rules(text, ctx) -> {"tool", "args"} or None. Pure, no Ollama needed (tests use it).
Router.handle(text) -> (reply, route) runs the tool or chats, and updates the context.
"""
import re
import time

import config
from brain.context import Context
from tools import registry


def _log(msg):
    print(f"[KIKU] {msg}", flush=True)


# ---- numbers ----

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "last": -1, "latest": 1, "newest": 1,
            "most recent": 1, "top": 1}
CARDINALS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10}
NUM = (r"(?P<num>most recent|" + "|".join(list(ORDINALS) + list(CARDINALS))
       + r"|\d+(?:st|nd|rd|th)?)")


def word_to_int(word):
    """"second" -> 2, "3rd" -> 3, "two" -> 2, "7" -> 7, "last" -> -1, else None."""
    w = word.strip().lower()
    w = re.sub(r"^number\s+", "", w)
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", w)
    if m:
        return int(m.group(1))
    return ORDINALS.get(w, CARDINALS.get(w))


# ---- text cleanup ----

_LEAD = re.compile(r"^(?:please|can you|could you|would you|will you|kiku|hey|ok|okay|now|and|so|just|"
                   r"i want you to|i'd like you to|go ahead and)\s+")
_TRAIL = re.compile(r"\s+(?:please|for me|now|thanks|thank you)$")


def normalize(text):
    t = text.lower().strip()
    t = re.sub(r"\s+dot\s+(?=[a-z])", ".", t)           # "youtube dot com" -> "youtube.com"
    t = re.sub(r"\.(?=\s|$)", " ", t)                    # sentence periods, keep domain dots
    t = re.sub(r"[^a-z0-9.'\- ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for pat in (_LEAD, _TRAIL):
        while True:
            new = pat.sub("", t)
            if new == t:
                break
            t = new
    return t


def _strip_article(s):
    return re.sub(r"^(?:the|my|a|an)\s+", "", s.strip())


# ---- rules: (pattern, builder(match, ctx) -> {tool, args} | None) ----

def _call(tool, **args):
    return {"tool": tool, "args": {k: v for k, v in args.items() if v is not None}}


def _stop(m, ctx):
    return _call("stop")


def _back(m, ctx):
    return _call("back")


def _close(m, ctx):
    t = _strip_article(m.group("t") or "")
    t = re.sub(r"^(?:this|that|current)\s+", "", t)
    if t in ("", "it", "this", "that", "window"):
        return _call("close")
    if t in ("tab", "this tab"):
        return _call("close", target="tab")
    return _call("close", target=t)


def _paragraph(m, ctx):
    g = m.groupdict()
    mode = "summarize" if re.search(r"summ|sum up", g["verb"]) else "analyze"
    return _call("analyze_paragraph", n=word_to_int(g.get("num") or g.get("num2")), mode=mode)


def _check_email(m, ctx):
    c = m.groupdict().get("c")
    c = _strip_article(c) if c else None
    if c and (c.split()[0] in ("you", "i", "me", "we") or len(c.split()) > 4):
        return None   # "did you send the email" is not an inbox check
    if c in ("anyone", "anybody", "someone", "somebody"):
        c = None
    return _call("check_email", contact=c)


def _read_email(m, ctx):
    what = m.groupdict().get("what")
    if what == "one" and not ctx.focus["emails"]:
        return None   # "the second one" with no email list in focus: let the LLM decide
    return _call("read_email", n=word_to_int(m.group("num")))


def _website(m, ctx):
    x = _strip_article(m.group("x"))
    return _call("open_website", site=x) if x else None


_DOMAIN = re.compile(r"\.[a-z]{2,}(?:/\S*)?$")


def _open(m, ctx):
    x = _strip_article(m.group("x"))
    kind = (m.group("kind") or "").strip()
    if not x:
        return None
    if x in ("it", "this", "that", "them", "this one", "that one"):
        return _call("clarify")   # don't guess what "it" is
    if kind in ("website", "site", "page") or x in config.KNOWN_SITES or _DOMAIN.search(x):
        return _call("open_website", site=x)
    return _call("open_app", app=x)


_EMAILWORD = r"(?:e-?mails?|mails?|messages?)"
RULES = [
    (r"(?:stop|stop it|stop talking|cancel|never ?mind|shut up|be quiet|quiet|enough|abort)", _stop),
    (r"(?:go )?back|(?:go )?back (?:a|one) page|(?:go to )?(?:the )?previous page", _back),
    (r"close(?: (?P<t>.+))?", _close),
    # paragraphs: "summarize the second paragraph", "analyze paragraph 3"
    (r"(?P<verb>analy[sz]e|summari[sz]e|sum up|explain|break down)(?: me)? (?:the )?"
     r"(?:" + NUM + r" paragraph|paragraph (?:number )?" + NUM.replace("num", "num2") + r")"
     r"(?: on (?:this|the) page)?", _paragraph),
    (r"(?:give me )?(?P<verb>a summary) of (?:the )?" + NUM + r" paragraph", _paragraph),
    # email checks
    (r"(?:did|has) (?P<c>.+?) (?:e-?mail(?:ed)?|mail(?:ed)?|message(?:d)?|write|wrote|send|sent)"
     r"(?: me)?(?: (?:an? |any )?" + _EMAILWORD + r")?(?: yet| today)?", _check_email),
    (r"(?:did|have) i (?:get|got|receive|received) (?:an? |any )?(?:new )?" + _EMAILWORD
     + r" from (?P<c>.+?)(?: yet| today)?", _check_email),
    (r"(?:is there|are there|any) (?:an? |any )?(?:new )?" + _EMAILWORD + r" from (?P<c>.+?)(?: yet| today)?",
     _check_email),
    (r"(?:check|show|list)(?: me)? (?:my )?(?:new )?(?:" + _EMAILWORD + r"|inbox)(?: from (?P<c>.+))?",
     _check_email),
    (r"(?:do i have|have i got) (?:any )?(?:new )?" + _EMAILWORD + r"(?: from (?P<c>.+))?", _check_email),
    # read the nth email
    (r"(?:read|open|show)(?: me)? (?:the |my )?" + NUM + r" (?P<what>one|" + _EMAILWORD + r")", _read_email),
    (r"(?:read|open|show)(?: me)? (?:the )?" + _EMAILWORD + r" (?:number )?" + NUM, _read_email),
    # websites and apps
    (r"(?:go to|goto|navigate to|browse to|visit|take me to)(?: the)? (?P<x>.+?)(?: website| site)?", _website),
    (r"(?:open|launch|start|fire up|bring up|pull up)(?: up)? (?P<x>.+?)"
     r"(?P<kind> app| application| website| site| page)?", _open),
]
RULES = [(re.compile(p + "$"), fn) for p, fn in RULES]


def match_rules(text, ctx):
    """Fast path: {"tool", "args"} or None if no rule fits."""
    t = normalize(text)
    if not t:
        return None
    for pat, fn in RULES:
        m = pat.match(t)
        if m:
            r = fn(m, ctx)
            if r is not None:
                return r
    return None


# ---- router ----
# Pseudo tool "clarify": Kiku asks back instead of guessing.

_PRONOUNS = {"it", "this", "that", "them", "one"}
# The LLM may pick these arg-less tools only if the text has one of these words.
_NEEDS_WORD = {"stop": r"stop|cancel|quiet|enough|shut|abort|never ?mind",
               "back": r"back|previous|return", "close": r"close|quit|exit"}
_QUESTION = re.compile(r"(?:what|what's|whats|who|who's|why|how|when|where|which|whose|is|are|was|were|"
                       r"does|do you|can you tell|tell me|explain|and|define|should|could|would)\b")


def is_question(text):
    """Questions skip intent parsing and go straight to chat (saves ~0.7 s, avoids misroutes)."""
    t = re.sub(r"^(?:please|kiku|hey|ok|okay|so)[\s,]+", "", text.lower().strip(" ,.!?"))
    return bool(_QUESTION.match(t))

class Router:
    def __init__(self, llm=None, ctx=None):
        self.llm = llm
        self.ctx = ctx or Context()

    def route(self, text):
        t = time.monotonic()
        r = match_rules(text, self.ctx)
        via = "rule"
        if r is None and is_question(text):
            r = {"tool": "chat", "args": {}}
        if r is None:
            if self.llm and self.llm.available:
                r, via = self.llm.parse_intent(text, self.ctx), "llm"
                word = _NEEDS_WORD.get(r["tool"])
                if any(str(v).lower() in _PRONOUNS for v in r["args"].values()):
                    _log(f"route: intent {r} has only a pronoun, asking")
                    r = {"tool": "clarify", "args": {}}   # "open it": don't guess
                elif word and not re.search(word, normalize(text)):
                    _log(f"route: dropped unlikely intent {r}, chatting instead")
                    r = {"tool": "chat", "args": {}}      # "and of italy?" -> stop
            else:
                r, via = {"tool": "chat", "args": {}}, "none"
        return {**r, "via": via, "ms": (time.monotonic() - t) * 1000}

    def handle(self, text):
        """Route, run, remember. Returns (reply text, route dict)."""
        t0 = time.monotonic()
        route = self.route(text)
        t1 = time.monotonic()
        if route["tool"] == "clarify":
            reply = "Which one do you mean?"
        elif route["tool"] == "chat":
            if self.llm and self.llm.available:
                reply = self.llm.chat(text, self.ctx)
            else:
                reply = "Sorry, my language model is offline."
        else:
            reply = registry.run(route["tool"], route["args"], self.ctx)
        t2 = time.monotonic()
        if route["tool"] != "stop":
            self.ctx.add_turn(text, reply, route["tool"])
        _log(f"route: {route['via']} -> {route['tool']} {route['args']} | route {route['ms']:.0f} ms, "
             f"{'chat' if route['tool'] == 'chat' else 'tool'} {(t2 - t1) * 1000:.0f} ms, "
             f"total {(t2 - t0) * 1000:.0f} ms")
        return reply, route
