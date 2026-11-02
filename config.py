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

# Brain (Ollama)
OLLAMA_HOST = "http://127.0.0.1:11434"
LLM_MODEL = "qwen3:1.7b"
LLM_THINK = False
LLM_KEEP_ALIVE = "30m"
LLM_NUM_CTX = 2048
LLM_MAX_TOKENS = 128

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
