"""All Kiku configuration lives here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
MODELS_DIR = ROOT / "models"
SECRETS_DIR = ROOT / "secrets"

# Face images drawn by the shell overlay
FACE_IDLE = ASSETS_DIR / "idle.png"
FACE_ACTION = ASSETS_DIR / "action.png"

# D-Bus (kiku-shell extension on the session bus)
DBUS_NAME = "org.kiku.Shell"
DBUS_PATH = "/org/kiku/Shell"
DBUS_IFACE = "org.kiku.Shell"

# Hotkeys (registered by kiku-shell via Main.wm.addKeybinding)
# install.sh writes these into the extension's gsettings
HOTKEY_PANIC = "<Control><Alt>k"
HOTKEY_PTT = "<Control><Alt>space"   # fires on press only (toggle-style)
PANIC_PHRASE = "kiku stop"

# Overlay motion (ms), scaled by distance
MOVE_MIN_MS = 120
MOVE_MAX_MS = 350

# Desktop control (control/, tools/open_app.py)
HOVER_MS = 120               # icon rests on a target this long before acting
TYPE_CPS = 40                # typing speed, characters per second (fast but visible)
APP_WAIT_S = 10.0            # wait this long for a launched app's window
OVERVIEW_WAIT_S = 1.5        # wait for Activities to open after Super
APP_MATCH_MIN = 0.75         # fuzzy score (0..1) needed to accept a spoken app name
ATSPI_MAX_NODES = 4000       # accessibility tree walk budget per search
ATSPI_TIMEOUT_S = 2.0
OCR_MIN_SCORE = 0.6
# Spoken name -> app name, for names fuzzy matching can't get. Names, not ids: ids differ
# between snap/deb/flatpak (VS Code is com.microsoft.VSCode.desktop here).
APP_ALIASES = {
    "browser": "firefox",
    "web browser": "firefox",
    "file manager": "files",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
}

# Brain (Ollama)
OLLAMA_HOST = "http://127.0.0.1:11434"
LLM_MODEL = "qwen3:1.7b"
LLM_THINK = False
LLM_KEEP_ALIVE = "30m"
LLM_NUM_CTX = 2048
LLM_MAX_TOKENS = 128          # chat replies
LLM_INTENT_MAX_TOKENS = 64    # parse_intent JSON
LLM_TIMEOUT_S = 30
CHAT_MAX_SENTENCES = 2
CONTEXT_TURNS = 6             # remembered user/assistant turns

# Router fast path: names that mean a website, not an app ("open youtube")
KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://en.wikipedia.org",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "chatgpt": "https://chatgpt.com",
    "google maps": "https://maps.google.com",
    "google drive": "https://drive.google.com",
    "stack overflow": "https://stackoverflow.com",
}

# Mic + energy VAD (voice/mic.py)
MIC_DEVICE = None            # sounddevice device index/name; None = system default
SAMPLE_RATE = 16000
VAD_FRAME_MS = 30
VAD_CALIBRATE_S = 1.0        # noise floor measured at startup (stay quiet)
VAD_MARGIN_DB = 10.0         # speech = this many dB above the noise floor
VAD_MIN_THRESHOLD_DB = -50.0 # never set the threshold below this (dBFS)
VAD_MAX_THRESHOLD_DB = -25.0 # ...or above this (normal speech is about -30..-15 dBFS)
VAD_START_FRAMES = 3         # loud frames in a row to start an utterance
VAD_SILENCE_MS = 700         # quiet this long ends the utterance
VAD_PREROLL_MS = 300         # audio kept from before the onset
VAD_MIN_SPEECH_MS = 300      # shorter blips are dropped
VAD_MAX_S = 15.0             # hard cap per utterance

# STT (faster-whisper)
STT_MODEL = "base.en"
STT_DEVICE = "cpu"
STT_COMPUTE_TYPE = "int8"
STT_PROMPT = "Kiku."         # nudges Whisper to spell the wake word right

# Activation: wake word at the start of an utterance, or the push-to-talk hotkey.
# Includes common Whisper misspellings. Matched on lowercased words, punctuation removed.
WAKE_WORDS = ["kiku", "kikoo", "kikou", "tiku", "tikoo", "kiko", "kikku", "kicku", "keku", "kee koo",
              "key koo", "ki ku", "kick you", "kick u", "cuckoo", "coo coo", "kuku", "chiku", "qiku"]
WAKE_PREFIXES = ["hey", "hi", "ok", "okay"]   # "hey kiku ..." also works
PTT_WINDOW_S = 8.0           # after the PTT hotkey, the next utterance needs no wake word

# TTS (Kyutai Pocket TTS, CPU). Voice: a built-in name ("eve", "alba", ...) or a .wav/.safetensors path
TTS_VOICE = "eve"
TTS_LANGUAGE = None          # None = default English model
SPEAKER_DEVICE = None        # sounddevice output device; None = system default

# I/O source: "local" (laptop mic + speaker) or "remote" (Jetson FRIDAY over net/)
IO_SOURCE = "local"
REMOTE_HOST = "0.0.0.0"
REMOTE_PORT = 8765

# Firefox bridge (browser/): the WebExtension connects to this WebSocket and must send the token.
# The token is made once and kept in secrets/browser_token; paste it into the extension's options.
BROWSER_HOST = "127.0.0.1"
BROWSER_PORT = 8766
BROWSER_TOKEN_FILE = SECRETS_DIR / "browser_token"


def _browser_token():
    import secrets
    try:
        return BROWSER_TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(24)
        BROWSER_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_TOKEN_FILE.write_text(token + "\n")
        BROWSER_TOKEN_FILE.chmod(0o600)
        return token


BROWSER_TOKEN = _browser_token()
BROWSER_CALL_TIMEOUT_S = 5.0     # default timeout for one extension command
BROWSER_CONNECT_WAIT_S = 8.0     # after Firefox starts, wait this long for the extension to connect
WEB_LOAD_TIMEOUT_S = 15.0        # open_website: wait this long for the page to finish loading
FIREFOX_APP = "firefox"          # spoken app name handed to open_app
# AT-SPI name of Firefox's address bar (needs toolkit-accessibility; else Ctrl+L + an estimate).
# Fuzzy-matched, so "Search with Google or enter address" matches too.
URLBAR_NAME = "Search or enter address"

# Page reader (tools/page_reader.py)
PAGE_CHUNK_TOKENS = 300          # RAG chunk size (estimated tokens: words * 1.3)
PAGE_TOP_K = 3                   # chunks sent to the LLM per question
PAGE_CONTEXT_TOKENS = 1100       # max page text per prompt (fits LLM_NUM_CTX with the reply)
PAGE_CACHE_PAGES = 8             # parsed pages kept (per URL)
PAGE_ANSWER_SENTENCES = 3
PAGE_MAX_TOKENS = 160            # reply length for page answers
PAGE_MIN_P_CHARS = 40            # shorter <p> are not content paragraphs (empty/infobox bits)
PAGE_CONNECT_WAIT_S = 3.0        # extension not connected yet (just started): wait this long
SCAN_LINE_MS = 220               # text selection steps one visual line this often while thinking
SCAN_RECT_MS = 900               # fallback scan line sweep (when selection fails)

# Gmail (tools/gmail.py): read only, through the Firefox bridge. Selectors live in tools/gmail.py.
GMAIL_HOST = "mail.google.com"
GMAIL_READY_WAIT_S = 12.0        # after opening Gmail, wait this long for its search box
GMAIL_RESULTS_WAIT_S = 8.0       # after Enter, wait this long for search results
GMAIL_OPEN_WAIT_S = 6.0          # after clicking a row, wait this long for the email body
GMAIL_LIST_MAX = 5               # rows read out per list
GMAIL_READ_ALOUD_WORDS = 120     # shorter emails are read out; longer ones are summarized
GMAIL_ROW_PAUSE_S = 0.6          # per-row highlight when there's no voice to pace it
