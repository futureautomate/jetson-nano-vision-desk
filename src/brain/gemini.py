"""The "brain" layer — periodically ask Gemini to reason about the scene.

A plain HTTPS POST to the Generative Language API (works on the Nano's Python 3.6
with only `requests`). Sends a downscaled JPEG frame + the current detections,
asks for STRICT JSON: a one-line scene description + an action decision. Returns
a dict; on any error/timeout it returns None and the caller keeps using the local
reflex rules (graceful degradation).

Config via env (see .env.example): GEMINI_API_KEY, GEMINI_MODEL (default
gemini-2.5-flash — NOT gemini-2.0-flash), GEMINI_INTERVAL_S.
"""
import base64
import json
import logging
import os
import time

log = logging.getLogger("vision.brain")

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

_SYSTEM = (
    "You are the reasoning layer of a Jetson Nano 'vision desk' device. You receive a "
    "camera frame of someone's desk plus a list of on-device detections. Decide what the "
    "device should do. Reply with STRICT JSON only, no markdown, with this exact shape:\n"
    '{"scene": "<one short sentence describing what is happening>",\n'
    ' "lamp": <true|false>,            // should the desk lamp be on?\n'
    ' "point_at": "<label or null>",   // which detected object the pointer arm should aim at\n'
    ' "alert": <true|false>,           // raise a buzzer/notification alert?\n'
    ' "status": "idle"|"active"|"alert",\n'
    ' "say": "<short note for the on-screen HUD>"}\n'
    "Note: the device ALREADY switches the desk lamp on automatically whenever a person is at the "
    "desk — set lamp=true only if extra light would help even otherwise (e.g. someone reading in a "
    "dim room); lamp=false is fine the rest of the time. Be conservative with alert=true: only for "
    "something genuinely unusual (e.g. an unfamiliar person lingering and facing the camera)."
)


class GeminiBrain(object):
    def __init__(self, api_key=None, model=None, timeout_s=12):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
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
            log.info("Gemini brain disabled (no GEMINI_API_KEY / no requests) — local reflexes only")
        else:
            log.info("Gemini brain enabled — model %s", self.model)

    def think(self, jpeg_bytes, detections):
        """Return a decision dict, or None on any failure."""
        if not self.enabled:
            return None
        det_summary = [{"label": d["label"], "conf": round(d["confidence"], 2),
                        "cx": round(d["cx"], 2), "area": round(d["area"], 3)} for d in detections]
        parts = [{"text": _SYSTEM},
                 {"text": "Detections: " + json.dumps(det_summary)}]
        if jpeg_bytes:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(jpeg_bytes).decode("ascii")}})
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        url = _ENDPOINT.format(model=self.model, key=self.api_key)
        try:
            r = self._requests.post(url, json=body, timeout=self.timeout_s)
            if r.status_code != 200:
                log.warning("Gemini HTTP %s: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            txt = data["candidates"][0]["content"]["parts"][0]["text"]
            decision = json.loads(txt)
            return self._normalize(decision)
        except Exception as e:
            log.warning("Gemini call failed: %s", e)
            return None

    @staticmethod
    def _normalize(d):
        out = {
            "scene": str(d.get("scene", ""))[:200],
            "lamp": bool(d.get("lamp", False)),
            "point_at": (str(d["point_at"]) if d.get("point_at") not in (None, "", "null") else None),
            "alert": bool(d.get("alert", False)),
            "status": d.get("status") if d.get("status") in ("idle", "active", "alert") else "idle",
            "say": str(d.get("say", ""))[:200],
            "ts": time.time(),
        }
        return out
