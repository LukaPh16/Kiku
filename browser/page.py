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

    def blocks(self, timeout=10, **limits):
        """Main content blocks (no screen targets: those come from scroll_into_view).
        {blocks: [{id, kind: p|h|li, level, text, rect, in_view}], title, url, view}"""
        return self.bridge.call("get_blocks", timeout=timeout, **limits)

    def scroll_into_view(self, el, block="center"):
        res = self.bridge.call("scroll_into_view", element_id=el["id"], block=block)
        vp = self.viewport(res["view"])
        el.update(rect=res["rect"], in_view=res["in_view"], viewport=vp)
        el["target"] = vp.target(el.get("text", "")[:60], res["rect"], {"id": el["id"]})
        return el

    def line_ranges(self, el, max_lines=60):
        """Visual lines of el: [{start, end, target, in_view}] (offsets as in select())."""
        res = self.bridge.call("line_ranges", element_id=el["id"], max_lines=max_lines)
        vp = self.viewport(res["view"])
        for ln in res["lines"]:
            ln["target"] = vp.target("line", ln["rect"])
            ln["in_view"] = vp.clip(ln["target"].rect) is not None
        return res["lines"]

    def select(self, el, start, end):
        """Select text [start, end) of el. Returns {text, targets: [global rects of the selection]}."""
        res = self.bridge.call("select_range", element_id=el["id"], start=start, end=end)
        vp = self.viewport(res["view"])
        return {"text": res["text"], "targets": [vp.target(res["text"][:60], r) for r in res["rects"]]}

    def clear_selection(self):
        self.bridge.call("clear_selection")

    def text(self, el=None, max_chars=20000, inner=False):
        """Text of el (or the page). inner=True: rendered text with line breaks, hidden parts skipped."""
        args = {"element_id": el["id"]} if el else {}
        return self.bridge.call("dom_text", max_chars=max_chars, inner=inner, **args)["text"]

    def rows(self, row, fields, limit=50):
        """Visible rows matching row (selector or list, first that matches wins), each with
        fields {name: text}, field_rects, lines (its text lines), target and field_targets."""
        res = self.bridge.call("get_rows", row=row, fields=fields, limit=limit)
        if not res["rows"]:
            return []
        vp = self.viewport(res["view"])
        for r in res["rows"]:
            r["target"] = vp.target("row", r["rect"], {"id": r["id"]})
            r["field_targets"] = {k: vp.target(k, fr) for k, fr in r["field_rects"].items()}
        return res["rows"]

    def focused(self):
        """{has_focus, element: describe() of document.activeElement or None}."""
        return self.bridge.call("get_focus")

    def click(self, el):
        """Scroll el into view if needed, move Kiku's icon to it, then click it in the page."""
        if not el.get("in_view"):
            self.scroll_into_view(el)
        if self.actions is not None:
            self.actions.point_at(el["target"])
            self.shell.click_pulse()
        self.bridge.call("click", element_id=el["id"])
        _log(f"clicked {el['id']} {el.get('text', '')[:40]!r}")
