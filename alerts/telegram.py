"""
Telegram Alert-Sender.
Nutzt python-telegram-bot für Benachrichtigungen bei BOS-Events.
"""
from telegram import Bot
from telegram.error import TelegramError
from loguru import logger

from analysis.models import AlertEvent


EMOJI = {
    "BOS_UP":          "🟢",
    "BOS_DOWN":        "🔴",
    "POTENTIAL_SETUP": "⚠️",
}


def format_alert_message(event: AlertEvent) -> str:
    """Formatiert ein AlertEvent als Telegram-Nachricht."""
    emoji     = EMOJI.get(event.event_type, "📊")
    direction = "↑ LONG Setup" if "UP" in event.event_type else "↓ SHORT Setup"

    msg = (
        f"{emoji} *{event.symbol}* | `{event.timeframe}`\n"
        f"*{direction}*\n"
        f"Preis: `{event.price:.4f}`\n"
        f"Zeit: `{event.time.strftime('%Y-%m-%d %H:%M')}`\n"
    )
    if event.details:
        msg += f"_{event.details}_"
    return msg


class TelegramAlerter:
    """
    Async-fähige Wrapper-Klasse für Telegram-Benachrichtigungen.
    Wird global in main.py instanziiert und vom Background-Monitor genutzt.
    """

    def __init__(self, token: str, chat_id: str, enabled: bool = True) -> None:
        self.token   = token
        self.chat_id = chat_id
        self.enabled = enabled

    async def send_async(self, event: AlertEvent) -> None:
        """Sendet einen Alert asynchron via Telegram."""
        if not self.enabled:
            logger.debug(
                f"[Telegram disabled] Alert: {event.event_type} @ "
                f"{event.symbol} {event.timeframe}"
            )
            return

        if not self.token or not self.chat_id:
            logger.warning("Telegram nicht konfiguriert (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID fehlen).")
            return

        bot     = Bot(token=self.token)
        message = format_alert_message(event)
        try:
            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
            )
            logger.info(
                f"Telegram Alert gesendet: {event.event_type} | "
                f"{event.symbol} {event.timeframe}"
            )
        except TelegramError as exc:
            logger.error(f"Telegram Fehler: {exc}")

    async def send_startup_message(self, symbols: list[str], timeframes: list[str]) -> None:
        """Sendet eine Startup-Nachricht wenn der Monitor startet."""
        if not self.enabled or not self.token or not self.chat_id:
            return

        msg = (
            f"🚀 *Trading Dashboard gestartet*\n"
            f"Symbole: {', '.join(symbols)}\n"
            f"Timeframes: {', '.join(timeframes)}"
        )
        try:
            bot = Bot(token=self.token)
            await bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode="Markdown",
            )
        except TelegramError as exc:
            logger.error(f"Telegram Startup-Nachricht fehlgeschlagen: {exc}")
