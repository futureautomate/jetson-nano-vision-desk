"""Jetson Nano Vision Desk — entry point.

    python3 -m src.main             # print config (pin map, GPIO/Gemini availability)
    python3 -m src.main --selftest  # exercise every actuator (lamp, servo sweep, buzzer)
    python3 -m src.main --demo       # the loop: detectnet -> reflex reactions (+ Gemini if a key is set).
                                     #   prints to the console for now; the PyQt5 HUD comes in Phase 3.

Runs anywhere: on the Jetson it uses Jetson.GPIO + jetson-inference; on a laptop /
CI (or with COMPANION_SIMULATE=1) it uses mock backends, so nothing here crashes.
"""
import argparse
import logging
import os
import re
import time

from src.hardware import pins
from src.hardware.board import gpio


# --- tiny .env loader (no python-dotenv dependency; Py3.6-safe) -------------
def _load_env():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.environ.get("COMPANION_ENV"),
                  os.path.join(here, ".env"),
                  os.path.expanduser("~/jetson-vision-desk-data/.env")]
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    # strip dotenv-style inline comments (a '#' preceded by whitespace),
                    # so `KEY=20   # note` -> "20" and not "20   # note"
                    v = re.split(r"\s#", v.strip(), 1)[0].strip()
                    os.environ.setdefault(k.strip(), v)
            logging.getLogger("vision").info("loaded env from %s", path)
            return path
    return None


def _print_config():
    print("Jetson Nano Vision Desk")
    print("-" * 48)
    print("GPIO backend :", "Jetson.GPIO (real)" if gpio.real else "MOCK (no hardware)")
    print("Gemini       :", "enabled (key set)" if os.environ.get("GEMINI_API_KEY") else "disabled (no GEMINI_API_KEY)")
    print("\nActuators:")
    for name, spec in pins.ACTUATORS.items():
        print("  {:<11} {:<12} pin {:<3}  {}".format(name, spec["iface"], spec["pin"], spec.get("desc", "")))
    print("\nCamera: USB (jetson-inference detectnet on /dev/video0)  — install jetson-inference on the Nano")
    print("-" * 48)


def _selftest():
    from src.hardware.actuators import Actuators
    print("== SELFTEST ==  GPIO:", "Jetson.GPIO" if gpio.real else "MOCK")
    act = Actuators()
    try:
        print("lamp ON");  act.lamp.on();  time.sleep(0.7)
        print("lamp OFF"); act.lamp.off(); time.sleep(0.3)
        print("mood light ON"); act.mood_light.on(); time.sleep(0.5)
        print("mood light OFF"); act.mood_light.off(); time.sleep(0.3)
        print("servo sweep ...")
        for ang in (0, 45, 90, 135, 180, 90):
            print("  servo -> %d" % ang); act.pointer.set_angle(ang); time.sleep(0.6)
        print("buzzer beep");  act.buzzer.beep(0.15); time.sleep(0.4)
        print("buzzer alert"); act.buzzer.alert(); time.sleep(1.2)
        print("state:", act.state())
        print("SELFTEST OK")
        return 0
    finally:
        act.shutdown(); gpio.cleanup()


def _demo():
    """Vision -> reflex reactions, with Gemini overlaying decisions if a key is set.
    Console output for now; Phase 3 swaps the console for the PyQt5 HUD on the DWIN screen."""
    from src.hardware.actuators import Actuators
    from src.vision.detector import Detector
    from src.brain.gemini import GeminiBrain

    no_person_timeout = float(os.environ.get("NO_PERSON_TIMEOUT_S", "20"))
    gem_interval = float(os.environ.get("GEMINI_INTERVAL_S", "8"))
    conf = float(os.environ.get("DETECT_CONFIDENCE", "0.5"))

    act = Actuators()
    det = Detector(camera="/dev/video0", confidence=conf)
    brain = GeminiBrain()
    print("== DEMO ==  detector:", "real" if det.real else "MOCK",
          "| GPIO:", "real" if gpio.real else "MOCK", "| Gemini:", "on" if brain.enabled else "off")
    if not det.real:
        print("(jetson-inference not installed here — install it on the Nano; this loop will idle)")

    last_person_t = 0.0
    last_gem_t = 0.0
    decision = None
    try:
        for img, dets in det.stream():
            now = time.time()
            people = Detector.count(dets, "person")
            if people:
                last_person_t = now

            # --- reflex layer ---
            lamp_should = (now - last_person_t) <= no_person_timeout and people > 0
            target = Detector.most_prominent(dets, prefer_label=(decision or {}).get("point_at"))
            # --- brain overlay (periodic) ---
            if brain.enabled and (now - last_gem_t) >= gem_interval and dets:
                last_gem_t = now
                jpeg = None
                if det.real and img is not None:
                    try:
                        import jetson.utils as ju  # type: ignore
                        import cv2  # type: ignore
                        arr = ju.cudaToNumpy(img)
                        small = cv2.resize(arr, (640, 360))
                        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGBA2BGR))
                        jpeg = buf.tobytes() if ok else None
                    except Exception:
                        jpeg = None
                d = brain.think(jpeg, dets)
                if d is not None:
                    decision = d
                    print("[GEMINI] %s | lamp=%s point_at=%s alert=%s — %s" % (
                        d["scene"], d["lamp"], d["point_at"], d["alert"], d["say"]))

            if decision:
                lamp_should = decision["lamp"]
                if decision["point_at"]:
                    t = next((x for x in dets if x["label"] == decision["point_at"]), None)
                    if t:
                        target = t
                act.set_status(decision["status"])
                if decision["alert"]:
                    act.buzzer.alert()
            else:
                act.set_status("active" if people else "idle")

            act.lamp.set(lamp_should)
            if target:
                act.pointer.point_at_fraction(target["cx"])

            if dets:
                print("fps=%.0f people=%d dets=%s lamp=%s servo=%.0f" % (
                    det.fps(), people, [d["label"] for d in dets][:6], act.lamp.is_on, act.pointer.angle))
            time.sleep(0.0 if det.real else 0.5)
    except KeyboardInterrupt:
        pass
    finally:
        act.shutdown(); det.close(); gpio.cleanup()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="src.main", description="Jetson Nano Vision Desk")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true", help="exercise every actuator")
    g.add_argument("--demo", action="store_true", help="run the vision -> reactions loop (console output)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_env()
    if args.selftest:
        return _selftest()
    if args.demo:
        return _demo()
    _print_config()
    print("\nNext: install jetson-inference on the Nano (Phase 1), wire the actuators (Phase 2),")
    print("then `python3 -m src.main --selftest` and `--demo`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
