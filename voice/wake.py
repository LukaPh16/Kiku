"""Wake word check on transcribed text ("Kiku, open Firefox" -> "open Firefox")."""
import re

import config


def _words(text):
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", " ")).split()


def _alt(phrases):
    # "kick you" also matches "kick-you", "Kick, you"
    return "|".join(r"[\s,\-]+".join(map(re.escape, p.split())) for p in phrases)


_WAKE_RE = re.compile(r"^\W*(?:(?:%s)\W+)?(?:%s)\b[\s,.!?;:\-]*(.*)$"
                      % (_alt(config.WAKE_PREFIXES), _alt(config.WAKE_WORDS)), re.I | re.S)


def strip_wake(text):
    """Return the command after a leading wake word ("" if only the wake word), or None."""
    m = _WAKE_RE.match(text.strip())
    return m.group(1).strip(" ,.!?;:") if m else None


def is_stop(command):
    """True for "stop" (as in "kiku stop")."""
    words = _words(command)
    return words in (["stop"], ["stop", "it"], ["stop", "talking"])


if __name__ == "__main__":
    cases = {
        "Kiku, open Firefox.": "open Firefox",
        "kikoo what time is it?": "what time is it",
        "Kick you, open Gmail": "open Gmail",
        "Cuckoo. Play music": "Play music",
        "Hey Kiku, hello there!": "hello there",
        "Ki-ku stop": "stop",
        "Kiku.": "",
        "kick the ball": None,
        "I said kiku": None,
        "": None,
    }
    bad = 0
    for text, want in cases.items():
        got = strip_wake(text)
        ok = got == want
        bad += not ok
        print(f"[KIKU] wake: {'ok ' if ok else 'BAD'} {text!r} -> {got!r}")
    assert is_stop("Stop.") and not is_stop("stop the music")
    print(f"[KIKU] wake: {'all passed' if not bad else f'{bad} failed'}")
    raise SystemExit(bad != 0)
