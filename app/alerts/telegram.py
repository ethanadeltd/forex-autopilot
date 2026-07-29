from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            # Avoid emoji encode errors on Windows cp1252 consoles
            safe = text.encode("ascii", errors="ignore").decode("ascii") or text
            logger.info("[alert-skip] %s", safe)
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text[:4000],
                        "disable_web_page_preview": True,
                    },
                )
                r.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
