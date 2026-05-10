"""On-device object detection via NVIDIA jetson-inference (detectnet).

On the Jetson this uses `jetson.inference.detectNet` + `jetson.utils.videoSource`
with the prebuilt SSD-Mobilenet-v2 (no training, ~20-30 FPS on the Nano GPU).
If those modules aren't importable (laptop / CI), it falls back to a mock that
yields nothing — so the rest of the app still runs.

Each detection is normalized to a plain dict:
    {"label": str, "confidence": float, "box": (l,t,r,b), "cx": float, "cy": float, "area": float}
where cx/cy/area are FRACTIONS of the frame (0..1) so downstream code is
resolution-independent (e.g. servo.point_at_fraction(det["cx"])).

Usage:
    det = Detector(camera="/dev/video0", confidence=0.5)
    for frame, detections in det.stream():
        ...
    det.close()
"""
import logging
import time

log = logging.getLogger("vision.detector")

# Default model: SSD-Mobilenet-v2 (COCO-ish 91 classes), bundled with jetson-inference.
DEFAULT_NETWORK = "ssd-mobilenet-v2"


class Detector(object):
    def __init__(self, camera="/dev/video0", network=DEFAULT_NETWORK, confidence=0.5,
                 width=1280, height=720):
        self.camera, self.network, self.confidence = camera, network, confidence
        self.width, self.height = width, height
        self._net = None
        self._src = None
        self._utils = None
        self.real = False
        try:
            import jetson.inference as ji      # type: ignore
            import jetson.utils as ju          # type: ignore
            self._ji, self._utils = ji, ju
            self._net = ji.detectNet(network, ["--confidence=%s" % confidence])
            self._src = ju.videoSource(camera, argv=["--input-width=%d" % width,
                                                     "--input-height=%d" % height])
            self.real = True
            log.info("jetson-inference detectNet '%s' on %s", network, camera)
        except Exception as e:
            log.warning("jetson-inference unavailable (%s) — Detector is a no-op mock", e)

    @property
    def class_desc(self):
        if self.real:
            return self._net  # net.GetClassDesc(id) available on the object
        return None

    def stream(self):
        """Yield (cuda_frame, [detection dicts]) forever. cuda_frame is a jetson.utils image
        on the real backend (use jetson.utils.cudaToNumpy(...) for OpenCV), None on the mock."""
        if not self.real:
            while True:
                time.sleep(0.1)
                yield None, []
        while True:
            img = self._src.Capture()
            if img is None:
                continue
            raw = self._net.Detect(img, overlay="none")
            W = float(img.width or self.width)
            H = float(img.height or self.height)
            dets = []
            for d in raw:
                label = self._net.GetClassDesc(d.ClassID)
                cx = ((d.Left + d.Right) / 2.0) / W
                cy = ((d.Top + d.Bottom) / 2.0) / H
                area = max(0.0, (d.Right - d.Left)) * max(0.0, (d.Bottom - d.Top)) / (W * H)
                dets.append({"label": label, "confidence": float(d.Confidence),
                             "box": (float(d.Left), float(d.Top), float(d.Right), float(d.Bottom)),
                             "cx": cx, "cy": cy, "area": area})
            yield img, dets

    @staticmethod
    def most_prominent(detections, prefer_label=None):
        """Pick the detection to point at: biggest box, optionally biased toward `prefer_label`."""
        if not detections:
            return None
        def score(d):
            s = d["area"] * d["confidence"]
            if prefer_label and d["label"] == prefer_label:
                s *= 2.0
            return s
        return max(detections, key=score)

    @staticmethod
    def count(detections, label):
        return sum(1 for d in detections if d["label"] == label)

    def fps(self):
        if self.real:
            try:
                return float(self._net.GetNetworkFPS())
            except Exception:
                return 0.0
        return 0.0

    def close(self):
        # jetson.utils sources/nets clean up on GC; nothing required here.
        self._src = None
        self._net = None
