"""Jetson Nano Vision Desk — entry point.

    python3 -m src.main             # print config (camera / Gemini / Telegram availability)
    python3 -m src.main --demo      # vision -> Gemini identify loop, console output
    python3 -m src.main --hud       # ...rendered on the PyQt5 HUD (DWIN HDMI screen)

Runs anywhere: on the Jetson it uses jetson-inference; on a laptop / CI it uses
a no-op mock Detector, so nothing here crashes. The Gemini and Telegram layers
degrade gracefully (no key → disabled).
"""
import argparse
import logging
import os
import re
import time


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
    print("Jetson Nano Vision Desk — Object Identifier")
    print("-" * 48)
    print("Gemini       :", "enabled (key set)" if os.environ.get("GEMINI_API_KEY")
          else "disabled (no GEMINI_API_KEY)")
    print("Telegram     :", "enabled" if (os.environ.get("TELEGRAM_BOT_TOKEN")
                                          and os.environ.get("TELEGRAM_CHAT_ID"))
          else "disabled (no TELEGRAM_BOT_TOKEN/CHAT_ID)")
    print("Camera       : USB (jetson-inference detectnet on /dev/video0)")
    print("-" * 48)


def _demo():
    """Run the identify loop with console output. Useful for headless dev / soak testing."""
    from src.engine import Engine, S_SHOWING, S_WATCHING
    eng = Engine(draw_overlay=False)
    print("== DEMO ==  detector:", "real" if eng.det.real else "MOCK",
          "| Gemini:", "on" if eng.brain.enabled else "off",
          "| Telegram:", "on" if eng.notifier.enabled else "off")
    if not eng.det.real:
        print("(jetson-inference not installed here — install it on the Nano; this loop will idle)")
    idle = 0.0 if eng.det.real else 0.4
    last_state = None
    try:
        for s in eng.run():
            if s.state != last_state:
                if s.state == S_WATCHING:
                    print("watching '%s' (need %.1fs steady)..." % (s.watch_label, eng.stable_s))
                elif s.state == "IDENTIFYING":
                    print("identifying...")
                last_state = s.state
            if s.result and s.state == S_SHOWING:
                r = s.result
                print("\n== %s ==  [%s]" % (r["name"], r["kind"]))
                print("  %s" % r["summary"])
                for f in r["facts"]:
                    print("   • %s" % f)
                print("(remove the object from frame to scan another)\n")
                # console demo: auto-clear so we can loop on another item
                eng.clear()
                last_state = None
            time.sleep(idle)
    finally:
        eng.shutdown()
    return 0


def _hud():
    """Launch the PyQt5 HUD on the DWIN HDMI screen (set HUD_WINDOWED=1 to run in a window)."""
    from src.ui.hud import run_hud
    return run_hud()


def main(argv=None):
    p = argparse.ArgumentParser(prog="src.main", description="Jetson Nano Vision Desk — Object Identifier")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true", help="vision -> Gemini identify loop (console)")
    g.add_argument("--hud",  action="store_true", help="...on the PyQt5 HUD on the DWIN screen")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_env()
    if args.demo:
        return _demo()
    if args.hud:
        return _hud()
    _print_config()
    print("\nRun:  --demo (console) | --hud (DWIN screen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
