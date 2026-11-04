"""Conversation memory: last few turns + what Kiku is currently focused on."""
import collections

import config


class Context:
    def __init__(self):
        self.turns = collections.deque(maxlen=config.CONTEXT_TURNS)   # (user, assistant, tool)
        # app: last app opened/used; page: URL in the browser; emails: last listed emails;
        # email: the email open right now (tools/gmail.py)
        self.focus = {"app": None, "page": None, "emails": None, "email": None}

    def add_turn(self, user, assistant, tool="chat"):
        self.turns.append((user, assistant, tool))

    def messages(self, chat_only=False):
        """Turns as Ollama chat messages. chat_only drops tool turns: a small model copies
        "Opening youtube." replies into unrelated answers."""
        out = []
        for user, assistant, tool in self.turns:
            if chat_only and tool != "chat":
                continue
            out.append({"role": "user", "content": user})
            out.append({"role": "assistant", "content": assistant or "(done)"})
        return out

    def describe_focus(self):
        """One line for the LLM prompt, e.g. 'app=firefox, page=https://..., 3 emails listed'."""
        parts = []
        if self.focus["app"]:
            parts.append(f"app={self.focus['app']}")
        if self.focus["page"]:
            parts.append(f"page={self.focus['page']}")
        if self.focus["emails"]:
            parts.append(f"{len(self.focus['emails'])} emails listed")
        return ", ".join(parts) or "nothing"
