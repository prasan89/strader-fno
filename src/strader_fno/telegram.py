from __future__ import annotations

import re

from .http import ApiError, request_json


class Telegram:
    def __init__(self, bot_token: str, chat_id: str, transport=request_json):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", bot_token) or not chat_id:
            raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your untracked .env")
        self._token = bot_token
        self._chat_id = chat_id
        self._transport = transport

    def send(self, text: str) -> None:
        response = self._transport(
            f"https://api.telegram.org/bot{self._token}/sendMessage", service="Telegram",
            payload={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
        )
        if response.get("ok") is not True:
            parameters = response.get("parameters") or {}
            raise ApiError("Telegram", response.get("error_code"), parameters.get("retry_after"))
