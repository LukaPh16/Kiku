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
HOTKEY_PANIC = "<Control><Alt>k"
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

# STT (faster-whisper)
STT_MODEL = "base.en"
STT_DEVICE = "cpu"
STT_COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000

# TTS (Piper)
PIPER_VOICE = MODELS_DIR / "piper" / "en_US-lessac-medium.onnx"

# I/O source: "local" (laptop mic + speaker) or "remote" (Jetson FRIDAY over net/)
IO_SOURCE = "local"
REMOTE_HOST = "0.0.0.0"
REMOTE_PORT = 8765
