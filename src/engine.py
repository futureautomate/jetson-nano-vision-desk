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
# IMPORTANT: cv2 must be imported BEFORE any jetson-inference / jetson-utils symbols,
# otherwise on aarch64 you hit `ImportError: libgomp.so.1: cannot allocate memory in
# static TLS block` the first time cv2 loads — CUDA eats the TLS slots first.
# (LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1 in the systemd unit is a belt-and-braces.)
import cv2  # noqa: F401  (must be the very first non-stdlib import)

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
        # how long the same label must persist in frame before we ask Gemini
        self.stable_s   = float(os.environ.get("STABLE_S",    "5.0"))
        # how long the frame must be clear (no prominent object) before re-arming for the next scan
        self.clear_s    = float(os.environ.get("CLEAR_S",     "3.0"))
        # minimum time the result stays on screen, so the user has time to read it even if they
        # whip the object away the moment Gemini returns
        self.show_min_s = float(os.environ.get("SHOW_MIN_S",  "5.0"))
        self.conf       = float(os.environ.get("DETECT_CONFIDENCE", "0.4"))

        self.det = Detector(camera="/dev/video0", confidence=self.conf,
                            overlay=("box,labels,conf" if draw_overlay else "none"))
        self.brain = GeminiBrain()
        self.notifier = TelegramNotifier()

        # state machine
        self.state           = S_IDLE
        self.watch_label     = None
        self.watch_start     = 0.0
        self.result          = None
        self.result_shown_at = 0.0
        self.absent_since    = None

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
        """JPEG of the current frame for Gemini. Loop thread, cheap. Sends the camera's native
        resolution at high JPEG quality — Gemini needs the detail to read brand markings."""
        if not self.det.real:
            log.warning("_grab_jpeg: detector is mock, returning None")
            return None
        if img is None:
            log.warning("_grab_jpeg: img is None, returning None")
            return None
        try:
            import jetson.utils as ju   # type: ignore
            # cv2 already imported at module top (before CUDA libs — fixes aarch64 TLS issue)
            arr = ju.cudaToNumpy(img)
            code = cv2.COLOR_RGBA2BGR if (arr.ndim == 3 and arr.shape[2] == 4) else cv2.COLOR_RGB2BGR
            bgr = cv2.cvtColor(arr, code)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                log.warning("_grab_jpeg: cv2.imencode returned False")
                return None
            return buf.tobytes()
        except Exception as e:
            log.warning("_grab_jpeg failed: %r (arr shape=%s)", e,
                        getattr(arr, "shape", "?") if "arr" in dir() else "n/a")
            return None

    def _kick_off_gemini(self, img, hint_label):
        if self._gem_inflight:
            return
        self._gem_inflight = True
        jpeg = self._grab_jpeg(img)
        if jpeg:
            try:
                # debug: dump the exact JPEG we're handing Gemini, so we can see what it saw
                with open("/tmp/last_gemini_input.jpg", "wb") as f:
                    f.write(jpeg)
            except Exception:
                pass
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
        self.result_shown_at = time.time()
        self.absent_since = None
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
                    # The result is locked on screen. NO new Gemini calls fire from here — even
                    # if a different object appears — until the frame's been clear for clear_s AND
                    # we've shown the result for at least show_min_s (so the user has time to read it).
                    show_elapsed = now - self.result_shown_at
                    if not prominent:
                        if self.absent_since is None:
                            self.absent_since = now
                        elif (now - self.absent_since) >= self.clear_s and show_elapsed >= self.show_min_s:
                            self.clear()
                    else:
                        self.absent_since = None

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
