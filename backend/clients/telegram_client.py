"""
Telegram Bot API client for spread alerts.
"""

import logging
from typing import Optional

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_CLIENT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def send_message(text: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a Telegram message via Bot API.
    If chat_id is not provided, falls back to TELEGRAM_CHAT_ID from config.
    """
    token = TELEGRAM_BOT_TOKEN
    target_chat = (chat_id or TELEGRAM_CHAT_ID or "").strip()

    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping Telegram message")
        return False

    if not target_chat:
        logger.warning("TELEGRAM_CHAT_ID not set, skipping Telegram message")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=_CLIENT_TIMEOUT) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram message sent to %s", target_chat)
                return True
            else:
                logger.warning("Telegram API error: %s", data)
                return False
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False
