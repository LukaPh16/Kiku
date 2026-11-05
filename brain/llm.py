"""Ollama client for the local model: short chat answers + structured intent parsing."""
import json
import re
import threading
import time

import config
import perf
from tools import registry


def _log(msg):
    print(f"[KIKU] llm: {msg}", flush=True)


CHAT_SYSTEM = ("You are Kiku, a friendly desktop assistant on the user's Ubuntu laptop. "
               "You speak your answers out loud, so answer in at most two short sentences. "
               "No lists, markdown, or emoji. In this chat you cannot open, search, or do anything on the "
               "computer, so never say you are doing so; just answer.")

INTENT_SYSTEM = """You turn a voice command into one tool call for a desktop assistant.
Tools:
{tools}
- chat(): questions, small talk, or anything no tool fits

Currently focused on: {focus}
Rules: n is a 1-based number (-1 = last). Use the user's words for app, site and contact names.
Reply with JSON only."""


class Llm:
    def __init__(self):
        import ollama
        self.client = ollama.Client(host=config.OLLAMA_HOST, timeout=config.LLM_TIMEOUT_S)
        self._last_use = time.monotonic()
        self._closed = threading.Event()
        self.available = self.warmup()
        threading.Thread(target=self._keep_warm, name="kiku-llm-warm", daemon=True).start()

    def _keep_warm(self):
        """Ping Ollama after LLM_REWARM_S idle, before keep_alive unloads the model (a cold load
        costs ~2 s on the next command). Also notices Ollama starting after Kiku."""
        while not self._closed.wait(30):
            if time.monotonic() - self._last_use >= config.LLM_REWARM_S:
                was = self.available
                self.available = self.warmup(quiet=was)
                if self.available and not was:
                    _log("Ollama is up now")

    def close(self):
        self._closed.set()

    def _chat(self, messages, max_tokens, temperature, fmt=None, stage="llm"):
        self._last_use = time.monotonic()
        with perf.stage(stage):
            r = self.client.chat(model=config.LLM_MODEL, messages=messages, think=config.LLM_THINK,
                                 format=fmt, keep_alive=config.LLM_KEEP_ALIVE,
                                 options={"num_ctx": config.LLM_NUM_CTX, "num_predict": max_tokens,
                                          "temperature": temperature})
        return r.message.content or ""

    def warmup(self, quiet=False):
        """Load the model into RAM so the first real request is fast. quiet: log failures only."""
        t = time.monotonic()
        try:
            self._chat([{"role": "user", "content": "hi"}], max_tokens=1, temperature=0, stage="llm warm-up")
        except Exception as e:
            if not quiet:
                _log(f"WARNING: Ollama not reachable at {config.OLLAMA_HOST} ({e!r}); only fast-path rules will work")
            else:
                _log(f"WARNING: Ollama stopped answering ({e!r})")
            return False
        if not quiet:
            _log(f"{config.LLM_MODEL} warm in {(time.monotonic() - t) * 1000:.0f} ms")
        return True

    def chat(self, text, ctx=None):
        """Short spoken answer (max CHAT_MAX_SENTENCES sentences)."""
        system = CHAT_SYSTEM + (f" Currently focused on: {ctx.describe_focus()}." if ctx else "")
        msgs = [{"role": "system", "content": system}]
        msgs += ctx.messages(chat_only=True) if ctx else []
        msgs.append({"role": "user", "content": text})
        t = time.monotonic()
        try:
            reply = self._chat(msgs, config.LLM_MAX_TOKENS, temperature=0.6, stage="llm chat")
        except Exception as e:
            _log(f"chat failed: {e!r}")
            return "Sorry, my brain isn't responding."
        reply = short(reply)
        _log(f"chat {(time.monotonic() - t) * 1000:.0f} ms: {reply!r}")
        return reply

    def ask(self, system, prompt, history=(), sentences=None, max_tokens=None, temperature=0.4):
        """One-off answer for tools (page reader). history: earlier chat messages. Raises on failure."""
        msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": prompt}]
        t = time.monotonic()
        reply = short(self._chat(msgs, max_tokens or config.LLM_MAX_TOKENS, temperature, stage="llm answer"),
                      sentences)
        _log(f"ask {(time.monotonic() - t) * 1000:.0f} ms: {reply!r}")
        return reply

    def parse_intent(self, text, ctx=None):
        """{"tool": name, "args": {...}} via structured output; tool "chat" if nothing fits."""
        names = list(registry.TOOLS) + ["chat"]
        props = {"tool": {"type": "string", "enum": names}}
        for tool in registry.TOOLS.values():
            for k, spec in tool.args.items():
                props.setdefault(k, {kk: vv for kk, vv in spec.items() if kk != "required"})
        schema = {"type": "object", "properties": props, "required": ["tool"]}
        system = INTENT_SYSTEM.format(tools=registry.describe(),
                                      focus=ctx.describe_focus() if ctx else "nothing")
        msgs = [{"role": "system", "content": system}]
        if ctx and ctx.turns:   # last turn only: enough for "do that again", cheap for a 1.7B model
            msgs += ctx.messages()[-2:]
        msgs.append({"role": "user", "content": text})
        t = time.monotonic()
        try:
            raw = self._chat(msgs, config.LLM_INTENT_MAX_TOKENS, temperature=0, fmt=schema, stage="llm intent")
            data = json.loads(raw)
        except Exception as e:
            _log(f"parse_intent failed: {e!r}")
            return {"tool": "chat", "args": {}}
        tool = data.pop("tool", "chat")
        args = registry.clean_args(tool, data) if tool != "chat" else {}
        if args is None:   # unknown tool or missing a required arg
            _log(f"intent {tool} {data} incomplete, falling back to chat")
            tool, args = "chat", {}
        _log(f"intent {(time.monotonic() - t) * 1000:.0f} ms: {tool} {args}")
        return {"tool": tool, "args": args}


def short(text, n=None):
    """Clean model output and keep the first n sentences."""
    n = n or config.CHAT_MAX_SENTENCES
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[*_#`]+", "", text).replace("\n", " ").strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()
