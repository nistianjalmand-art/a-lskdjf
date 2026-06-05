"""
Konfiguration – lädt Credentials aus .env, Rest ist statisch.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── MT5 Credentials (optional) ───────────────────────────────────────────────
# Nur nötig, falls das Terminal beim Backend-Start noch nicht eingeloggt ist.
MT5_LOGIN:    str = os.getenv("MT5_LOGIN",    "")
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER:   str = os.getenv("MT5_SERVER",   "")

# ── Telegram Alerts ──────────────────────────────────────────────────────────
TELEGRAM_TOKEN:    str  = os.getenv("TELEGRAM_TOKEN",    "")
TELEGRAM_CHAT_ID:  str  = os.getenv("TELEGRAM_CHAT_ID",  "")
TELEGRAM_ENABLED:  bool = os.getenv("TELEGRAM_ENABLED",  "false").lower() == "true"

# ── Struktur-Analyse (Pivot-Erkennung) ───────────────────────────────────────
PIVOT_LENGTH: int = int(os.getenv("PIVOT_LENGTH", "1"))   # Pivot Left/Right Länge

# ── Background-Monitor ────────────────────────────────────────────────────────
# Welche Symbole + Timeframes sollen laufend auf BOS überwacht werden?
MONITOR_SYMBOLS:    list[str] = ["XAUUSD"]
MONITOR_TIMEFRAMES: list[str] = ["5m", "15m", "1h"]
MONITOR_CANDLE_COUNT: int = 300            # Kerzen je Monitor-Durchlauf
MONITOR_INTERVAL_SECONDS: int = int(os.getenv("MONITOR_INTERVAL", "10"))

# ── Chart-Defaults ───────────────────────────────────────────────────────────
DEFAULT_SYMBOL:       str = "XAUUSD"
DEFAULT_TIMEFRAME:    str = "5m"
DEFAULT_CANDLE_COUNT: int = 500

# ── Verfügbare Timeframes (UI-Labels) ────────────────────────────────────────
TIMEFRAMES: list[str] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
