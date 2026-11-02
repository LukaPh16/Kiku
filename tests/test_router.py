"""Router fast-path tests. No Ollama needed.

  venv/bin/python -m tests.test_router          # rules only
  venv/bin/python -m tests.test_router --llm    # also send non-rule phrases to Ollama (prints, no asserts)
Also works under pytest.
"""
import sys

from brain.context import Context
from brain.router import match_rules, normalize, word_to_int


def call(tool, **args):
    return {"tool": tool, "args": args}


# phrase (as Whisper writes it, wake word already removed) -> expected {tool, args}
CASES = [
    # apps
    ("Open Firefox.", call("open_app", app="firefox")),
    ("open the terminal", call("open_app", app="terminal")),
    ("Launch Spotify, please.", call("open_app", app="spotify")),
    ("Can you open VS Code?", call("open_app", app="vs code")),
    ("start the calculator", call("open_app", app="calculator")),
    ("Open the files app", call("open_app", app="files")),
    ("fire up Steam", call("open_app", app="steam")),
    ("Switch to Files.", call("open_app", app="files")),
    ("switch to the terminal window", call("open_app", app="terminal")),
    # websites
    ("Open YouTube.", call("open_website", site="youtube")),
    ("open gmail", call("open_website", site="gmail")),
    ("Go to GitHub.", call("open_website", site="github")),
    ("go to reddit.com", call("open_website", site="reddit.com")),
    ("Open youtube dot com", call("open_website", site="youtube.com")),
    ("Visit the BBC website.", call("open_website", site="bbc")),
    ("take me to stack overflow", call("open_website", site="stack overflow")),
    ("open the weather site", call("open_website", site="weather")),
    ("Navigate to wikipedia.org.", call("open_website", site="wikipedia.org")),
    # paragraphs
    ("Summarize the second paragraph.", call("analyze_paragraph", n=2, mode="summarize")),
    ("analyze the third paragraph", call("analyze_paragraph", n=3, mode="analyze")),
    ("Analyse paragraph 4.", call("analyze_paragraph", n=4, mode="analyze")),
    ("summarise the 1st paragraph", call("analyze_paragraph", n=1, mode="summarize")),
    ("Explain the last paragraph on this page.", call("analyze_paragraph", n=-1, mode="analyze")),
    ("Give me a summary of the fifth paragraph.", call("analyze_paragraph", n=5, mode="summarize")),
    ("sum up paragraph number two", call("analyze_paragraph", n=2, mode="summarize")),
    # email checks
    ("Did Mom email me?", call("check_email", contact="mom")),
    ("did John Smith email me today", call("check_email", contact="john smith")),
    ("Has Sarah emailed me yet?", call("check_email", contact="sarah")),
    ("Did I get an email from Amazon?", call("check_email", contact="amazon")),
    ("any new emails from my boss", call("check_email", contact="boss")),
    ("Check my email.", call("check_email")),
    ("check my inbox please", call("check_email")),
    ("Do I have any new emails?", call("check_email")),
    # read nth email
    ("Read the second email.", call("read_email", n=2)),
    ("read my latest email", call("read_email", n=1)),
    ("Open the third message.", call("read_email", n=3)),
    ("read email number 4", call("read_email", n=4)),
    ("Read me the first email.", call("read_email", n=1)),
    # stop / back / close
    ("Stop.", call("stop")),
    ("stop talking", call("stop")),
    ("never mind", call("stop")),
    ("Go back.", call("back")),
    ("previous page", call("back")),
    ("Close.", call("close")),
    ("close this window", call("close")),
    ("Close the tab.", call("close", target="tab")),
    ("close firefox", call("close", target="firefox")),
    ("open it", call("clarify")),
]

# Not for the fast path: these must go to the LLM.
NO_RULE = [
    "What's the capital of France?",
    "Tell me a joke.",
    "How are you today?",
    "Read the second one.",           # no email list in focus
    "Did you send the email?",
    "What time is it?",
]


def test_word_to_int():
    for word, n in [("second", 2), ("3rd", 3), ("two", 2), ("7", 7), ("last", -1), ("latest", 1),
                    ("number five", 5), ("tenth", 10), ("banana", None)]:
        assert word_to_int(word) == n, (word, word_to_int(word), n)


def test_normalize():
    assert normalize("Kiku, open Firefox please.") == "open firefox"
    assert normalize("Go to YouTube dot com.") == "go to youtube.com"


def test_rules():
    bad = []
    for phrase, want in CASES:
        got = match_rules(phrase, Context())
        if got != want:
            bad.append((phrase, got, want))
    for phrase in NO_RULE:
        got = match_rules(phrase, Context())
        if got is not None:
            bad.append((phrase, got, None))
    for phrase, got, want in bad:
        print(f"[KIKU] test: BAD {phrase!r}\n         got  {got}\n         want {want}")
    assert not bad, f"{len(bad)} router cases failed"


def test_questions_skip_intent():
    from brain.router import is_question
    for q in ["What's the capital of France?", "and of Italy?", "How are you today?", "Tell me a joke.",
              "Is it going to rain?", "Who wrote Hamlet?"]:
        assert is_question(q), q
    for c in ["Open Firefox", "read the second one", "open it", "bring the music player up", "Kiku stop"]:
        assert not is_question(c), c


def test_followup_uses_focus():
    from brain.router import Router
    router = Router(llm=None)
    reply, route = router.handle("Did Mom email me?")
    assert route["tool"] == "check_email" and router.ctx.focus["emails"]
    reply, route = router.handle("Read the second one.")
    assert (route["tool"], route["args"], route["via"]) == ("read_email", {"n": 2}, "rule"), route
    reply, route = router.handle("open youtube")
    assert router.ctx.focus["page"] == "https://www.youtube.com", router.ctx.focus
    assert len(router.ctx.turns) == 3


def test_memory_keeps_last_6_turns():
    ctx = Context()
    for i in range(10):
        ctx.add_turn(f"u{i}", f"a{i}", "chat" if i % 2 else "open_app")
    assert [t[0] for t in ctx.turns] == [f"u{i}" for i in range(4, 10)]
    assert [m["content"] for m in ctx.messages(chat_only=True) if m["role"] == "user"] == ["u5", "u7", "u9"]


def run_llm():
    from brain.llm import Llm
    llm = Llm()
    if not llm.available:
        return
    for phrase in NO_RULE + ["Could you bring the music player up?", "I need to write some code, get my editor"]:
        r = llm.parse_intent(phrase, Context())
        print(f"[KIKU] test llm: {phrase!r} -> {r}")


if __name__ == "__main__":
    tests = [test_word_to_int, test_normalize, test_rules, test_questions_skip_intent, test_followup_uses_focus, test_memory_keeps_last_6_turns]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[KIKU] test: ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[KIKU] test: BAD {t.__name__}: {e}")
    print(f"[KIKU] test: {len(CASES)} phrases + {len(NO_RULE)} LLM-only phrases; "
          f"{'all passed' if not failed else f'{failed} tests failed'}")
    if "--llm" in sys.argv:
        run_llm()
    raise SystemExit(failed != 0)
