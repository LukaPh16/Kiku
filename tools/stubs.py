"""Stub tools: print their args. The rest are real now (see tools/): open_app, open_website,
the page reader, gmail (check_email, read_email, back) and close.
"""
from tools.registry import register


def _stub(name, **args):
    print(f"[KIKU] tool stub: {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})", flush=True)


@register("stop", "Stop talking / abort the current action")
def stop(ctx):
    _stub("stop")
    return ""

