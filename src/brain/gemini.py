"""The "brain" layer — ask Gemini to identify an object held up to the camera.

A plain HTTPS POST to the Generative Language API (works on the Nano's Python 3.6
with only `requests`). Sends a downscaled JPEG frame + a hint label from the
on-device detector, asks for STRICT JSON with the object's name + kind + summary
+ 3–5 facts. Returns a dict; on any error/timeout returns None.

Config via env (see .env.example): GEMINI_API_KEY, GEMINI_MODEL (default
gemini-2.5-flash — NOT gemini-2.0-flash).
"""
import base64
import json
import logging
import os
import time

log = logging.getLogger("vision.brain")

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

_SYSTEM_IDENTIFY = (
    "You are an object-identification assistant for a desk camera. The user holds up a single "
    "physical item — could be a book, gadget, consumer product, food package, art piece, tool, "
    "plant, lego build, currency, anything. Look at the image and identify what's actually there.\n"
    "\n"
    "**Look at SHAPE and TEXT.** First, determine the broad category from the overall shape and "
    "characteristic features:\n"
    "  • a video-game controller has thumbsticks + a button diamond / D-pad\n"
    "  • a Stream Deck / macro pad is a flat grid of identical square keys with no thumbsticks\n"
    "  • a TV remote is long and thin with a column of small buttons\n"
    "  • a book has a rectangular cover with title text\n"
    "  • a phone is flat and rectangular with a single screen\n"
    "Then READ any visible text, logos, brand names, or model numbers — those refine the answer "
    "from generic to specific. 'PlayStation 5' beats 'video-game controller'. 'iPhone 15 Pro' beats "
    "'cell phone'. But only commit to a specific brand/model if you can ACTUALLY see the branding.\n"
    "\n"
    "**HONESTY OVER SPECIFICITY.** If the image is unclear, motion-blurred, or the brand isn't "
    "readable, return a generic but accurate name ('purple video-game controller', 'a small "
    "rectangular electronic device') instead of inventing a specific product. Inventing a brand "
    "and model you can't actually see is the WORST possible answer — readers will trust your "
    "specifics and the false detail spreads. Be confident only when you have evidence; otherwise "
    "say what you can see.\n"
    "\n"
    "Reply with STRICT JSON only, no markdown, with this exact shape:\n"
    '{"name": "<the item\'s specific name (brand + model if visible) or honest generic description>",\n'
    ' "kind": "book|product|electronics|food|tool|art|toy|plant|currency|household|other",\n'
    ' "summary": "<one sentence describing what it is>",\n'
    ' "facts": ["<short fact>", "<short fact>", "<short fact>"]}\n'
    "Make 3–5 facts, each a short SPECIFIC line (not a paragraph). For an unknown specific item, "
    "give facts about the category instead (e.g. for 'a video-game controller (brand unclear)': "
    "common types of gaming controller, what thumbsticks/D-pads are for, typical USB/Bluetooth "
    "connectivity). Tailor to the kind:\n"
    "  BOOK         → author · year · what it's about · why it matters\n"
    "  PRODUCT      → brand · model · what it does · key feature · ballpark price\n"
    "  ELECTRONICS  → brand · model · chip/specs · common uses · ballpark price\n"
    "  FOOD         → brand · what it is · calories ballpark · origin · dietary notes\n"
    "  TOOL         → type · common uses · material/build · brand if visible\n"
    "  ART / OTHER  → artist/origin if known · medium · period/style · notable detail\n"
    "Always return JSON. Never refuse, but DO admit uncertainty in the name field when warranted."
)


class GeminiBrain(object):
    def __init__(self, api_key=None, model=None, timeout_s=15):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.environ.get("GEMINI_MODEL", "") or "gemini-2.5-flash").strip()
        self.timeout_s = timeout_s
        self.enabled = bool(self.api_key)
        self._requests = None
        if self.enabled:
            try:
                import requests  # type: ignore
                self._requests = requests
            except Exception as e:
                log.warning("`requests` not available (%s) — Gemini brain disabled", e)
                self.enabled = False
        if not self.enabled:
            log.info("Gemini brain disabled (no GEMINI_API_KEY / no requests)")
        else:
            log.info("Gemini brain enabled — model %s", self.model)

    def identify(self, jpeg_bytes, hint_label=None):
        """Identify the object visible in the frame. Returns
            {"name", "kind", "summary", "facts":[...], "ts"} on success, or None on failure.
        """
        if not self.enabled:
            return None
        parts = [{"text": _SYSTEM_IDENTIFY}]
        if hint_label:
            parts.append({"text":
                "Context (low-confidence — IGNORE if the image disagrees): a small on-device classifier "
                "with only ~90 generic COCO classes guessed this might be a '%s'. That classifier is "
                "frequently wrong on consumer electronics, books, packaged products, and anything "
                "domain-specific. Trust what you actually see in the image — especially any text or "
                "logos — over this hint." % hint_label})
        if jpeg_bytes:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(jpeg_bytes).decode("ascii")}})
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        url = _ENDPOINT.format(model=self.model, key=self.api_key)
        try:
            r = self._requests.post(url, json=body, timeout=self.timeout_s)
            if r.status_code != 200:
                log.warning("Gemini HTTP %s: %s", r.status_code, r.text[:300])
                return None
            data = r.json()
            txt = data["candidates"][0]["content"]["parts"][0]["text"]
            return self._normalize(json.loads(txt))
        except Exception as e:
            log.warning("Gemini call failed: %s", e)
            return None

    @staticmethod
    def _normalize(d):
        return {
            "name":    str(d.get("name", "Unknown"))[:200],
            "kind":    (str(d.get("kind", "other")).lower())[:32],
            "summary": str(d.get("summary", ""))[:400],
            "facts":   [str(f)[:200] for f in (d.get("facts") or []) if f][:6],
            "ts":      time.time(),
        }
