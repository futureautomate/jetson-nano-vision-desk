"""Telegram alerts — fire a snapshot + caption when something genuinely unusual happens.

Used by the engine on the rising edge of a Gemini `alert=true` decision (e.g. "an
unfamiliar person lingering and facing the camera"). A plain HTTPS POST to the Bot
API (works on the Nano's Python 3.6 with just `requests`); the send runs in a side
thread so the vision loop never stalls, and a cooldown stops it spamming if the
alert state flickers. Dormant (a no-op) unless TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
are set — see .env.example.

    TELEGRAM_BOT_TOKEN=123456:ABC-...        # from @BotFather
    TELEGRAM_CHAT_ID=987654321               # your chat id (message the bot, then
                                             #   curl https://api.telegram.org/bot<TOKEN>/getUpdates)
    TELEGRAM_ALERT_COOLDOWN_S=300            # min seconds between alert messages
"""
import logging
import os
import threading
import time

log = logging.getLogger("vision.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier(object):
    def __init__(self, token=None, chat_id=None, cooldown_s=None, timeout_s=15):
        self.token = (token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        try:
            self.cooldown_s = float(cooldown_s if cooldown_s is not None
                                    else os.environ.get("TELEGRAM_ALERT_COOLDOWN_S") or 300)
        except (TypeError, ValueError):
            self.cooldown_s = 300.0
        self.timeout_s = timeout_s
        self.enabled = bool(self.token and self.chat_id)
        self._requests = None
        self._last_sent = 0.0
        self._lock = threading.Lock()
        if self.enabled:
            try:
                import requests  # type: ignore
                self._requests = requests
            except Exception as e:
                log.warning("`requests` not available (%s) — Telegram disabled", e)
                self.enabled = False
        log.info("Telegram alerts %s%s", "enabled" if self.enabled else "disabled",
                 "" if self.enabled else " (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    def alert(self, jpeg_bytes, scene="", note=""):
        """Queue an alert message (snapshot + caption) — non-blocking, cooldown-limited."""
        if not self.enabled:
            return
        now = time.time()
        with self._lock:
            if now - self._last_sent < self.cooldown_s:
                log.debug("telegram alert suppressed (cooldown)")
                return
            self._last_sent = now
        caption = ("⚠ Vision Desk alert\n" + (scene or "") + (("\n— " + note) if note else "")).strip()
        threading.Thread(target=self._send, args=(jpeg_bytes, caption), daemon=True).start()

    def message(self, text):
        """Send a plain text message (e.g. a startup ping or a soak-test note)."""
        if not self.enabled:
            return
        threading.Thread(target=self._send, args=(None, text), daemon=True).start()

    # --- internals --------------------------------------------------------
    def _send(self, jpeg_bytes, text):
        try:
            if jpeg_bytes:
                url = _API.format(token=self.token, method="sendPhoto")
                r = self._requests.post(url, data={"chat_id": self.chat_id, "caption": text[:1024]},
                                        files={"photo": ("alert.jpg", jpeg_bytes, "image/jpeg")},
                                        timeout=self.timeout_s)
            else:
                url = _API.format(token=self.token, method="sendMessage")
                r = self._requests.post(url, data={"chat_id": self.chat_id, "text": text[:4096]},
                                        timeout=self.timeout_s)
            if r.status_code != 200:
                log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
            else:
                log.info("Telegram alert sent")
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
