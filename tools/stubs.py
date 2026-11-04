"""Stub tools (open_app, open_website and the page reader are real now, see tools/): print their args and update focus so follow-ups ("read the second one") work.

Replaced by real tools (gmail) in later steps.
"""
from tools.registry import register


def _stub(name, **args):
    print(f"[KIKU] tool stub: {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})", flush=True)


def _ordinal(n):
    if n == -1:
        return "last"
    return {1: "first", 2: "second", 3: "third"}.get(n, f"number {n}")


@register("check_email", "Check the inbox, optionally for emails from one contact",
          {"contact": {"type": "string", "required": False}})
def check_email(ctx, contact=None):
    _stub("check_email", contact=contact)
    ctx.focus["emails"] = [f"(stub email {i} from {contact or 'someone'})" for i in (1, 2, 3)]
    who = f" from {contact}" if contact else ""
    return f"Checking emails{who}. Gmail isn't connected yet."


@register("read_email", "Read the nth email from the last email list (1 = newest, -1 = last)",
          {"n": {"type": "integer"}})
def read_email(ctx, n):
    emails = ctx.focus["emails"]
    _stub("read_email", n=n, listed=len(emails or []))
    return f"I'd read the {_ordinal(n)} email, but Gmail isn't connected yet."


@register("stop", "Stop talking / abort the current action")
def stop(ctx):
    _stub("stop")
    return ""


@register("back", "Go back (previous page in the browser)")
def back(ctx):
    _stub("back", app=ctx.focus["app"])
    return "Going back."


@register("close", "Close the current window, tab, or a named app",
          {"target": {"type": "string", "required": False}})
def close(ctx, target=None):
    _stub("close", target=target or "window")
    return f"Closing {target or 'the window'}."
