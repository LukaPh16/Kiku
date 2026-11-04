"""The current Firefox tab, with element rects in global screen coords.

Wraps bridge commands; every element/paragraph dict gets "target" (control.locate.Target, global
logical px) next to the extension's CSS-px "rect". Clicks move Kiku's icon to the element first.
"""
from browser import geometry
from browser.bridge import BridgeError


def _log(msg):
    print(f"[KIKU] page: {msg}", flush=True)


class Page:
    def __init__(self, bridge, shell, actions=None):
        self.bridge, self.shell, self.actions = bridge, shell, actions

    def viewport(self, view):
        """geometry.Viewport for a `view` dict from the extension. Raises BridgeError if no Firefox window."""
        win = geometry.firefox_window(self.shell.list_windows())
        if win is None:
            raise BridgeError("no visible Firefox window")
        return geometry.viewport(win, view, self.shell.get_monitors())

    def _with_targets(self, items, vp):
        for d in items:
            d["target"] = vp.target(d.get("text", "")[:60], d["rect"], {"id": d["id"]})
        return items

    def url(self):
        return self.bridge.call("get_url")

    def find(self, selector=None, role=None, text=None, limit=20):
        args = {k: v for k, v in (("selector", selector), ("role", role), ("text", text)) if v}
        res = self.bridge.call("get_elements", limit=limit, **args)
        return self._with_targets(res["elements"], self.viewport(res["view"]))

    def paragraphs(self, min_chars=40):
        res = self.bridge.call("get_paragraphs", min_chars=min_chars)
        return self._with_targets(res["paragraphs"], self.viewport(res["view"]))

    def scroll_into_view(self, el, block="center"):
        res = self.bridge.call("scroll_into_view", element_id=el["id"], block=block)
        el.update(rect=res["rect"], in_view=res["in_view"])
        el["target"] = self.viewport(res["view"]).target(el.get("text", "")[:60], res["rect"], {"id": el["id"]})
        return el

    def select(self, el, start, end):
        """Select text [start, end) of el. Returns {text, targets: [global rects of the selection]}."""
        res = self.bridge.call("select_range", element_id=el["id"], start=start, end=end)
        vp = self.viewport(res["view"])
        return {"text": res["text"], "targets": [vp.target(res["text"][:60], r) for r in res["rects"]]}

    def clear_selection(self):
        self.bridge.call("clear_selection")

    def text(self, el=None, max_chars=20000):
        args = {"element_id": el["id"]} if el else {}
        return self.bridge.call("dom_text", max_chars=max_chars, **args)["text"]

    def click(self, el):
        """Scroll el into view if needed, move Kiku's icon to it, then click it in the page."""
        if not el.get("in_view"):
            self.scroll_into_view(el)
        if self.actions is not None:
            self.actions.point_at(el["target"])
            self.shell.click_pulse()
        self.bridge.call("click", element_id=el["id"])
        _log(f"clicked {el['id']} {el.get('text', '')[:40]!r}")
