"""Tool registry. A tool is name + description + args + fn(ctx, **args) -> reply text (spoken).

args maps arg name -> JSON-schema type dict, plus "required": bool (default True).
The router's rules and the LLM's parse_intent both return {tool, args} that run() executes.
"""
import re
from dataclasses import dataclass, field


def _log(msg):
    print(f"[KIKU] tools: {msg}", flush=True)


@dataclass
class Tool:
    name: str
    description: str
    fn: callable
    args: dict = field(default_factory=dict)

    def required(self):
        return [k for k, spec in self.args.items() if spec.get("required", True)]


TOOLS = {}


class Spoken(str):
    """A reply the tool already said out loud (tools/gmail.py speaks row by row); main.py won't repeat it."""
    spoken = True


def register(name, description, args=None):
    """Decorator: @register("open_app", "Open a desktop app", {"app": {"type": "string"}})"""
    def deco(fn):
        TOOLS[name] = Tool(name, description, fn, args or {})
        return fn
    return deco


def get(name):
    return TOOLS.get(name)


def clean_args(name, args):
    """Keep only declared args with values; None if a required arg is missing or the tool is unknown."""
    tool = TOOLS.get(name)
    if tool is None:
        return None
    out = {k: v for k, v in (args or {}).items() if k in tool.args and v not in (None, "")}
    for k, spec in tool.args.items():
        if k in out and spec.get("type") == "integer":
            try:
                out[k] = int(out[k])
            except (TypeError, ValueError):
                out.pop(k)
    if any(k not in out for k in tool.required()):
        return None
    return out


# ---- spoken errors: no exception text or traceback ever reaches TTS ----

# What a tool was doing, for "Sorry, something went wrong while ..."
DOING = {"open_app": "opening the app", "open_website": "opening the website",
         "analyze_paragraph": "reading the paragraph", "analyze_section": "reading that section",
         "analyze_page": "reading the page", "ask_page": "reading the page",
         "check_email": "checking your email", "read_email": "reading the email",
         "back": "going back", "close": "closing that"}
_CRASH = re.compile(r"Traceback \(most recent|File \"[^\"]+\", line \d|\w+(?:Error|Exception): |"
                    r"<[\w.]+ object at 0x")


def sorry(e, fallback):
    """What to say for a failure. control.actions.Failed("I couldn't switch to Files") carries a plain
    sentence: "Sorry, I couldn't switch to Files.". Any other exception's text is only logged:
    "Sorry, <fallback>."."""
    from control.actions import Failed
    msg = " ".join(str(e).split()) if isinstance(e, Failed) else ""
    if not msg:
        _log(f"not speaking error text {e!r}")
        msg = fallback
    return f"Sorry, {msg}" + ("" if msg[-1] in ".!?" else ".")


def speakable(text):
    """Last check before TTS: a reply that looks like a crash dump becomes a plain apology."""
    if text and _CRASH.search(text):
        _log(f"not speaking crash text {text[:120]!r}")
        return "Sorry, something went wrong."
    return text


def run(name, args, ctx):
    """Run a tool; returns its reply text ("" for none). Never raises."""
    from control.actions import Aborted
    tool = TOOLS[name]
    try:
        return tool.fn(ctx, **args) or ""
    except Aborted:
        _log(f"{name} aborted")
        return "Stopped."
    except Exception as e:
        _log(f"{name} failed: {e!r}")
        return f"Sorry, something went wrong while {DOING.get(name, 'doing that')}."


def describe():
    """Tool list for the LLM prompt."""
    lines = []
    for t in TOOLS.values():
        sig = ", ".join(k if s.get("required", True) else f"{k}?" for k, s in t.args.items())
        lines.append(f"- {t.name}({sig}): {t.description}")
    return "\n".join(lines)


# Register tools. Stubs print their args; real ones replace them step by step.
import tools.open_app  # noqa: E402,F401
import tools.open_website  # noqa: E402,F401
import tools.page_reader  # noqa: E402,F401
import tools.gmail  # noqa: E402,F401
import tools.stubs  # noqa: E402,F401
