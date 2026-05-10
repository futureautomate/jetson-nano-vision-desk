"""The vision -> reflex (-> Gemini brain) loop, decoupled from how it's presented.

`Engine.run()` is a generator that yields one `Snapshot` per processed frame. The
console front-end (`python3 -m src.main --demo`) prints each snapshot; the PyQt5
HUD (`src.ui.hud`) renders it. Pause + manual overrides (the HUD's touch buttons)
are honoured here, so both front-ends behave identically.

Runs anywhere: real Detector/Actuators on the Jetson, mock backends elsewhere.
Py3.6-safe (namedtuple, no dataclasses).
"""
import collections
import logging
import os
import threading
import time

from src.hardware.board import gpio
from src.hardware.actuators import Actuators
from src.vision.detector import Detector
from src.brain.gemini import GeminiBrain
from src.notify.telegram import TelegramNotifier

log = logging.getLogger("vision.engine")

# One per processed frame. `img` is a jetson.utils CUDA image (already overlaid with
# boxes when draw_overlay=True) or None on the mock backend. `gemini` is the latest
# decision dict (or None). `manual` is the set of active touch overrides.
Snapshot = collections.namedtuple(
    "Snapshot",
    "img dets people fps state gemini paused manual real_det real_gpio gemini_on")


class Engine(object):
    def __init__(self, draw_overlay=False):
        self.no_person_timeout = float(os.environ.get("NO_PERSON_TIMEOUT_S", "20"))
        self.gem_interval = float(os.environ.get("GEMINI_INTERVAL_S", "8"))
        self.conf = float(os.environ.get("DETECT_CONFIDENCE", "0.5"))
        self.act = Actuators()
        self.det = Detector(camera="/dev/video0", confidence=self.conf,
                            overlay=("box,labels,conf" if draw_overlay else "none"))
        self.brain = GeminiBrain()
        self.notifier = TelegramNotifier()
        self.paused = False
        self.manual = {}              # e.g. {"lamp": True/False}; absent key = automatic
        self._last_person_t = 0.0
        self._last_gem_t = 0.0
        self.decision = None
        # the Gemini call is a blocking HTTPS POST — run it in a side thread so the
        # vision loop never stalls; the result is picked up on a later frame.
        self._gem_inflight = False
        self._gem_pending = None      # decision dict waiting to be applied
        self._gem_lock = threading.Lock()
        self._person_present = False   # sticky "someone's at the desk" — for edge-triggered notifications
        log.info("engine: detector=%s gpio=%s gemini=%s telegram=%s",
                 "real" if self.det.real else "mock",
                 "real" if gpio.real else "mock",
                 "on" if self.brain.enabled else "off",
                 "on" if self.notifier.enabled else "off")

    # --- controls (called from the UI thread; plain bool/dict writes, no lock needed) ---
    def toggle_pause(self):
        self.paused = not self.paused
        log.info("engine %s", "PAUSED" if self.paused else "resumed")
        return self.paused

    def set_lamp_override(self, val):
        """val: True/False to force the lamp, None to hand it back to the reflex/brain logic."""
        if val is None:
            self.manual.pop("lamp", None)
        else:
            self.manual["lamp"] = bool(val)

    def center_servo(self):
        self.act.pointer.center()

    # --- Gemini brain (async) --------------------------------------------
    def _grab_jpeg(self, img):
        """Downscaled JPEG of the current frame for Gemini; cheap, done on the loop thread."""
        if not (self.det.real and img is not None):
            return None
        try:
            import jetson.utils as ju   # type: ignore
            import cv2                   # type: ignore
            arr = ju.cudaToNumpy(img)
            small = cv2.resize(arr, (640, 360))
            # videoSource frames are RGB(A); the JPEG encoder wants BGR
            code = cv2.COLOR_RGBA2BGR if (small.ndim == 3 and small.shape[2] == 4) else cv2.COLOR_RGB2BGR
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(small, code))
            return buf.tobytes() if ok else None
        except Exception as e:
            log.debug("gemini frame grab failed: %s", e)
            return None

    def _gem_request(self, jpeg, dets):
        """Runs in a side thread: the blocking HTTPS call. Stashes the result for the loop."""
        try:
            d = self.brain.think(jpeg, dets)
        except Exception as e:
            log.debug("gemini request failed: %s", e)
            d = None
        if d is not None:
            with self._gem_lock:
                self._gem_pending = d
        self._gem_inflight = False

    def _maybe_ask_gemini(self, img, dets, now):
        if not (self.brain.enabled and dets and not self._gem_inflight
                and (now - self._last_gem_t) >= self.gem_interval):
            return
        self._last_gem_t = now
        self._gem_inflight = True
        jpeg = self._grab_jpeg(img)
        threading.Thread(target=self._gem_request, args=(jpeg, list(dets)), daemon=True).start()

    def _apply_pending_gemini(self, img):
        if self._gem_pending is None:
            return
        with self._gem_lock:
            d, self._gem_pending = self._gem_pending, None
        rising_alert = d["alert"] and not (self.decision and self.decision.get("alert"))
        self.decision = d
        log.info("[GEMINI] %s | lamp=%s point_at=%s alert=%s — %s",
                 d["scene"], d["lamp"], d["point_at"], d["alert"], d["say"])
        if rising_alert and not self.paused:
            self.act.buzzer.alert()                                  # fire once on the transition
            self.notifier.alert(self._grab_jpeg(img),               # ...and a Telegram snapshot
                                scene=d.get("scene", ""), note=d.get("say", ""))

    # --- the loop ---------------------------------------------------------
    def run(self):
        """Yield a Snapshot per frame, forever. Swallows KeyboardInterrupt so the caller's
        `finally: engine.shutdown()` runs cleanly."""
        try:
            for img, dets in self.det.stream():
                now = time.time()
                people = Detector.count(dets, "person")
                if people:
                    self._last_person_t = now

                # --- reflex layer: fast, local, the FLOOR. "person present" is sticky for
                #     NO_PERSON_TIMEOUT_S after the last detection — debounces one-frame drops
                #     and acts as the lamp's grace period. A person at the desk -> lamp on now. ---
                person_now = (now - self._last_person_t) <= self.no_person_timeout
                lamp_should = person_now
                status = "active" if person_now else "idle"
                target = Detector.most_prominent(dets, prefer_label=(self.decision or {}).get("point_at"))

                # --- Gemini overlay: advisory — may *add* to the reflex, never undo it ---
                self._apply_pending_gemini(img)
                self._maybe_ask_gemini(img, dets, now)
                if self.decision:
                    lamp_should = lamp_should or self.decision["lamp"]   # can switch the lamp ON for other reasons
                    status = self.decision["status"]
                    if person_now and status == "idle":
                        status = "active"                                # don't dim below the person-present floor
                    if self.decision["point_at"]:
                        t = next((x for x in dets if x["label"] == self.decision["point_at"]), None)
                        if t:
                            target = t

                # manual touch overrides win over everything
                if "lamp" in self.manual:
                    lamp_should = self.manual["lamp"]

                # actuate — unless paused (paused still streams video + queries Gemini)
                if not self.paused:
                    self.act.set_status(status)
                    self.act.lamp.set(lamp_should)
                    if target:
                        self.act.pointer.point_at_fraction(target["cx"])

                # --- Telegram: ping (snapshot) on the rising edge of "person here, lamp on" ---
                arrived = person_now and not self._person_present and lamp_should and not self.paused
                self._person_present = person_now
                if arrived:
                    n = max(1, people)
                    self.notifier.event(self._grab_jpeg(img),
                                        "%d person%s at the desk — lamp on" % (n, "" if n == 1 else "s"))

                yield Snapshot(img=img, dets=dets, people=people, fps=self.det.fps(),
                               state=self.act.state(), gemini=self.decision,
                               paused=self.paused, manual=dict(self.manual),
                               real_det=self.det.real, real_gpio=gpio.real,
                               gemini_on=self.brain.enabled)
        except KeyboardInterrupt:
            pass

    def shutdown(self):
        try:
            self.act.shutdown()
        finally:
            try:
                self.det.close()
            finally:
                gpio.cleanup()
