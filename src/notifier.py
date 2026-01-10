"""Notification adapters."""

from __future__ import annotations

import os
from typing import Optional

import requests


class Notifier:
    def send(self, title: str, body: str) -> None:
        print(f"[NOTIFY] {title}: {body}")


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: Optional[str] = None) -> None:
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")

    def send(self, title: str, body: str) -> None:
        if not self.webhook_url:
            return
        payload = {"content": f"**{title}**\n{body}"}
        try:
            requests.post(self.webhook_url, json=payload, timeout=10)
        except requests.RequestException:
            return
