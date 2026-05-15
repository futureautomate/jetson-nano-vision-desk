"""Object/product identifier loop.

`detectnet` runs continuously on the camera feed. The moment it sees a stable
non-person detection (same label for `STABLE_S` seconds), the engine fires an
async Gemini query "what is this?" — Gemini returns `{name, kind, summary,
facts[]}`. The HUD shows the result alongside the live feed; once the object
leaves the frame for `CLEAR_S` seconds the engine returns to IDLE, ready for
the next one. Manual `request_identify()` jumps straight to IDENTIFYING using
whatever's currently in frame.

States:  IDLE → WATCHING → IDENTIFYING → SHOWING → IDLE
Runs anywhere: real Detector on the Jetson, mock elsewhere; degrades to
local-only (no identification) if Gemini's key/network is gone.
"""
import collections
import logging
import os
import threading
import time

from src.vision.detector import Detector
from src.brain.gemini import GeminiBrain
from src.notify.telegram import TelegramNotifier

log = logging.getLogger("vision.engine")

# Per-frame snapshot the HUD/console consume.
Snapshot = collections.namedtuple(
    "Snapshot",
    "img dets fps state watch_label watch_elapsed result real_det gemini_on backoff_remaining")

# State constants
S_IDLE        = "IDLE"
S_WATCHING    = "WATCHING"
S_IDENTIFYING = "IDENTIFYING"
S_SHOWING     = "SHOWING"

# detectnet labels we don't trigger on (a person isn't the product — they're holding it).
_IGNORE_LABELS = {"person"}


class Engine(object):
    def __init__(self, draw_overlay=False):
        self.stable_s = float(os.environ.get("STABLE_S", "2.0"))      # how long the same label must persist before we ask
        self.clear_s  = float(os.environ.get("CLEAR_S",  "3.0"))      # how long the frame must be clear before re-arming
        self.conf     = float(os.environ.get("DETECT_CONFIDENCE", "0.4"))

        self.det = Detector(camera="/dev/video0", confidence=self.conf,
                            overlay=("box,labels,conf" if draw_overlay else "none"))
        self.brain = GeminiBrain()
        self.notifier = TelegramNotifier()

        # state machine
        self.state         = S_IDLE
        self.watch_label   = None
        self.watch_start   = 0.0
        self.result        = None
        self.absent_since  = None

        # async Gemini plumbing
        self._gem_inflight = False
        self._gem_pending  = None        # (result_dict, jpeg_bytes) when a query returns
        self._gem_lock     = threading.Lock()
        self._gem_blocked_until = 0.0    # back-off after a failure (Gemini 429 / network) so we
                                         # don't slam the free-tier 20 RPM limit; reset on success
        self._gem_backoff_s = float(os.environ.get("GEMINI_BACKOFF_S", "30"))
        self._manual_kick  = False

        log.info("engine (identify mode): detector=%s gemini=%s telegram=%s",
                 "real" if self.det.real else "mock",
                 "on" if self.brain.enabled else "off",
                 "on" if self.notifier.enabled else "off")

    # --- controls (called from the UI thread) -------------------------------
    def request_identify(self):
        """Manual trigger — fires an identification on the *current* frame, even without stability."""
        self._manual_kick = True
        log.info("manual IDENTIFY requested")

    def clear(self):
        """Drop the current result and re-arm for the next object."""
        self.state = S_IDLE
        self.result = None
        self.watch_label = None
        self.absent_since = None
        log.info("cleared")

    # --- internals ----------------------------------------------------------
    def _most_prominent(self, dets):
        """Biggest non-person detection (area * confidence), or None."""
        cand = [d for d in dets if d["label"] not in _IGNORE_LABELS]
        return max(cand, key=lambda d: d["area"] * d["confidence"]) if cand else None

    def _grab_jpeg(self, img):
        """Downscaled JPEG for Gemini. Cheap; loop thread."""
        if not (self.det.real and img is not None):
            return None
        try:
            import jetson.utils as ju   # type: ignore
            import cv2                   # type: ignore
            arr = ju.cudaToNumpy(img)
            small = cv2.resize(arr, (640, 360))
            code = cv2.COLOR_RGBA2BGR if (small.ndim == 3 and small.shape[2] == 4) else cv2.COLOR_RGB2BGR
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(small, code))
            return buf.tobytes() if ok else None
        except Exception as e:
            log.debug("frame->jpeg failed: %s", e)
            return None

    def _kick_off_gemini(self, img, hint_label):
        if self._gem_inflight:
            return
        self._gem_inflight = True
        jpeg = self._grab_jpeg(img)
        threading.Thread(target=self._gem_request, args=(jpeg, hint_label), daemon=True).start()

    def _gem_request(self, jpeg, hint_label):
        """Side thread: the blocking HTTPS Gemini call."""
        try:
            res = self.brain.identify(jpeg, hint_label)
        except Exception as e:
            log.debug("gemini identify exception: %s", e)
            res = None
        with self._gem_lock:
            self._gem_pending = (res, jpeg)
        self._gem_inflight = False

    def _apply_pending(self):
        if self._gem_pending is None:
            return
        with self._gem_lock:
            (res, jpeg), self._gem_pending = self._gem_pending, None
        if res is None:
            # back off so we don't hammer the rate limit (free-tier gemini-2.5-flash is 20 RPM)
            self._gem_blocked_until = time.time() + self._gem_backoff_s
            log.info("[GEMINI] identification failed — backing off %.0fs", self._gem_backoff_s)
            self.state = S_IDLE
            self.watch_label = None
            return
        self.result = res
        self.state = S_SHOWING
        log.info("[GEMINI] %s  (%s)  — %s", res.get("name"), res.get("kind"), res.get("summary"))
        try:
            self.notifier.identified(jpeg, res)
        except Exception as e:
            log.debug("telegram identified() failed: %s", e)

    # --- the loop -----------------------------------------------------------
    def run(self):
        try:
            for img, dets in self.det.stream():
                now = time.time()
                prominent = self._most_prominent(dets)

                # always pick up any finished Gemini query first
                self._apply_pending()

                # manual trigger short-circuits state (and bypasses the back-off — user explicitly asked)
                if self._manual_kick:
                    self._manual_kick = False
                    if prominent and not self._gem_inflight:
                        self._gem_blocked_until = 0.0
                        self.watch_label = prominent["label"]
                        self.watch_start = now
                        self.state = S_IDENTIFYING
                        self._kick_off_gemini(img, prominent["label"])

                # state machine
                if self.state == S_IDLE:
                    if prominent:
                        self.state = S_WATCHING
                        self.watch_label = prominent["label"]
                        self.watch_start = now

                elif self.state == S_WATCHING:
                    if not prominent:
                        self.state = S_IDLE
                        self.watch_label = None
                    elif prominent["label"] != self.watch_label:
                        # different object — restart the watch
                        self.watch_label = prominent["label"]
                        self.watch_start = now
                    elif (now - self.watch_start) >= self.stable_s and not self._gem_inflight \
                            and now >= self._gem_blocked_until:
                        self.state = S_IDENTIFYING
                        self._kick_off_gemini(img, self.watch_label)

                elif self.state == S_IDENTIFYING:
                    pass  # waiting for _apply_pending — runs on every iteration above

                elif self.state == S_SHOWING:
                    if not prominent:
                        if self.absent_since is None:
                            self.absent_since = now
                        elif (now - self.absent_since) >= self.clear_s:
                            self.clear()
                    else:
                        self.absent_since = None
                        if prominent["label"] != self.watch_label:
                            # a new, different object — restart the watch (auto-rescan)
                            self.state = S_WATCHING
                            self.watch_label = prominent["label"]
                            self.watch_start = now
                            self.result = None

                watch_elapsed = (now - self.watch_start) if self.state == S_WATCHING else 0.0
                backoff_remaining = max(0.0, self._gem_blocked_until - now)
                yield Snapshot(img=img, dets=dets, fps=self.det.fps(),
                               state=self.state, watch_label=self.watch_label,
                               watch_elapsed=watch_elapsed, result=self.result,
                               real_det=self.det.real, gemini_on=self.brain.enabled,
                               backoff_remaining=backoff_remaining)
        except KeyboardInterrupt:
            pass

    def shutdown(self):
        try:
            self.det.close()
        except Exception:
            pass
