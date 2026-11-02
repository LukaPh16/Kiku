"""Tool registry. A tool is name + description + args + fn(ctx, **args) -> reply text (spoken).

args maps arg name -> JSON-schema type dict, plus "required": bool (default True).
The router's rules and the LLM's parse_intent both return {tool, args} that run() executes.
"""
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


def run(name, args, ctx):
    """Run a tool; returns its reply text ("" for none)."""
    tool = TOOLS[name]
    try:
        return tool.fn(ctx, **args) or ""
    except Exception as e:
        _log(f"{name} failed: {e!r}")
        return f"Sorry, {name.replace('_', ' ')} failed."


def describe():
    """Tool list for the LLM prompt."""
    lines = []
    for t in TOOLS.values():
        sig = ", ".join(k if s.get("required", True) else f"{k}?" for k, s in t.args.items())
        lines.append(f"- {t.name}({sig}): {t.description}")
    return "\n".join(lines)


# Stub tools for now (step 3): they print their args. Real ones come in later steps.
import tools.stubs  # noqa: E402,F401  registers the stubs
