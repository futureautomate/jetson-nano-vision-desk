"""Telegram log — each new identification sends a snapshot + a tidy caption to your bot.

Uses the Bot API directly via `requests` (works on the Nano's Python 3.6). The send
runs in a side thread so the vision loop never stalls, and a cooldown stops it
spamming if the same object flickers in/out of frame. Dormant (a no-op) unless
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set — see .env.example.

    TELEGRAM_BOT_TOKEN=123456:ABC-...        # from @BotFather
    TELEGRAM_CHAT_ID=987654321               # your chat id
    TELEGRAM_IDENTIFY_COOLDOWN_S=30          # min seconds between log messages
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
                                    else os.environ.get("TELEGRAM_IDENTIFY_COOLDOWN_S") or 30)
        except (TypeError, ValueError):
            self.cooldown_s = 30.0
        self.timeout_s = timeout_s
        self.enabled = bool(self.token and self.chat_id)
        self._requests = None
        self._last_sent = 0.0
        self._last_name = None
        self._lock = threading.Lock()
        if self.enabled:
            try:
                import requests  # type: ignore
                self._requests = requests
            except Exception as e:
                log.warning("`requests` not available (%s) — Telegram disabled", e)
                self.enabled = False
        log.info("Telegram %s%s",
                 "enabled" if self.enabled else "disabled",
                 "" if self.enabled else " (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    def identified(self, jpeg_bytes, result):
        """Send a snapshot + caption for an identification. Non-blocking, cooldown-limited,
        and de-dupes back-to-back duplicates."""
        if not self.enabled or not result:
            return
        name = (result.get("name") or "Unknown").strip()
        now = time.time()
        with self._lock:
            # cooldown
            if (now - self._last_sent) < self.cooldown_s:
                log.debug("telegram suppressed (cooldown)")
                return
            # de-dup (same name twice in a row, e.g. object flickered out/in)
            if name == self._last_name and (now - self._last_sent) < (self.cooldown_s * 3):
                log.debug("telegram suppressed (duplicate)")
                return
            self._last_sent = now
            self._last_name = name
        # caption — Telegram caps photo captions at 1024 chars
        kind = result.get("kind", "other")
        summary = result.get("summary", "")
        facts = result.get("facts") or []
        lines = ["🔍 *%s*" % name, "_%s_" % kind, summary, ""]
        for f in facts[:5]:
            lines.append("• " + f)
        caption = "\n".join(line for line in lines if line is not None)[:1024]
        threading.Thread(target=self._send, args=(jpeg_bytes, caption), daemon=True).start()

    def message(self, text):
        """Plain text — e.g. a startup/heartbeat ping. No cooldown."""
        if not self.enabled:
            return
        threading.Thread(target=self._send, args=(None, text), daemon=True).start()

    # --- internals ---------------------------------------------------------
    def _send(self, jpeg_bytes, text):
        try:
            if jpeg_bytes:
                url = _API.format(token=self.token, method="sendPhoto")
                r = self._requests.post(url, data={"chat_id": self.chat_id, "caption": text[:1024],
                                                   "parse_mode": "Markdown"},
                                        files={"photo": ("scan.jpg", jpeg_bytes, "image/jpeg")},
                                        timeout=self.timeout_s)
            else:
                url = _API.format(token=self.token, method="sendMessage")
                r = self._requests.post(url, data={"chat_id": self.chat_id, "text": text[:4096]},
                                        timeout=self.timeout_s)
            if r.status_code != 200:
                log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
            else:
                log.info("Telegram log sent")
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
