"""Jetson Nano Vision Desk — entry point.

    python3 -m src.main             # print config (pin map, GPIO/Gemini availability)
    python3 -m src.main --selftest  # exercise every actuator (lamp, servo sweep, buzzer)
    python3 -m src.main --demo      # the loop: detectnet -> reflex reactions (+ Gemini if a key is set), console output
    python3 -m src.main --hud       # same loop, but rendered on the PyQt5 HUD (the DWIN HDMI screen)

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
    """Vision -> reflex reactions (+ Gemini overlay if a key is set), printed to the console.
    Same loop the HUD runs — see src/engine.py."""
    from src.engine import Engine
    eng = Engine(draw_overlay=False)
    print("== DEMO ==  detector:", "real" if eng.det.real else "MOCK",
          "| GPIO:", "real" if gpio.real else "MOCK", "| Gemini:", "on" if eng.brain.enabled else "off")
    if not eng.det.real:
        print("(jetson-inference not installed here — install it on the Nano; this loop will idle)")
    idle = 0.0 if eng.det.real else 0.5
    try:
        for s in eng.run():
            if s.dets:
                print("fps=%.0f people=%d dets=%s lamp=%s servo=%.0f%s" % (
                    s.fps, s.people, [d["label"] for d in s.dets][:6],
                    s.state["lamp"], s.state["pointer_angle"], "  [PAUSED]" if s.paused else ""))
            time.sleep(idle)
    finally:
        eng.shutdown()
    return 0


def _hud():
    """Launch the PyQt5 HUD on the DWIN HDMI screen (set HUD_WINDOWED=1 to run in a window)."""
    from src.ui.hud import run_hud
    return run_hud()


def main(argv=None):
    p = argparse.ArgumentParser(prog="src.main", description="Jetson Nano Vision Desk")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true", help="exercise every actuator")
    g.add_argument("--demo", action="store_true", help="run the vision -> reactions loop (console output)")
    g.add_argument("--hud", action="store_true", help="run the loop with the PyQt5 HUD on the DWIN screen")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_env()
    if args.selftest:
        return _selftest()
    if args.demo:
        return _demo()
    if args.hud:
        return _hud()
    _print_config()
    print("\nRun:  --selftest (actuators) | --demo (vision loop, console) | --hud (vision loop, DWIN screen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
