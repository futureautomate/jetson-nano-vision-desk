# 👁️ Jetson Nano Vision Desk

An NVIDIA **Jetson Nano** with a webcam that watches your desk, sees who/what's there in **real time on its own GPU**, *physically* runs the room (desk lamp, a servo "pointer" arm, a buzzer, an LED strip), and — every few seconds — hands a frame to a **multimodal AI (Google Gemini)** that reasons about the whole scene and decides what to do. **Local model = fast reflexes; cloud VLM = the thinking layer.** Every part is already in the IOT inventory — **buy nothing**.

> Project brief & build phases: Notion → *Future Automation — Mission Control › Project Tracker › "Jetson Nano Vision Desk"*.
> This project re-uses the GPIO layer + deploy workflow from the (re-homed) [`Jetson-Nano-NeMo-Claw-Iot-Companion`](https://github.com/futureautomate/Jetson-Nano-NeMo-Claw-Iot-Companion) repo.

---

## How it works — two layers

**Layer 1 — on-device vision (fast reflexes, works offline):** `jetson-inference` `detectnet` (SSD-Mobilenet-v2, **prebuilt — zero training**) runs ~20–30 FPS on the Nano's 128-core GPU → bounding boxes + labels every frame → instant GPIO reactions (lamp on when a person's at the desk, the servo arm tracks the most prominent object, buzzer/LED on alert).

**Layer 2 — cloud "brain" (periodic, reasons about the scene):** every N seconds a downscaled frame + the detections go to **Gemini 2.5-flash** (multimodal, a plain HTTPS call — fine on the Nano's Python 3.6) → it returns a one-line **scene description** + a structured **action decision** that can override the simple reflex rules. Falls back to pure local reflexes if the network/API is down.

```
USB webcam (C270)
   │
   ▼
jetson-inference detectnet ─► detections ─► REFLEX rules ─► GPIO (lamp / servo / buzzer / LED)
   │                              │                                 ▲
   │ (every N s: frame+dets)       └─────────────────────────────────┼──► PyQt5 HUD on the DWIN HDMI screen
   ▼                              │                                 │
Gemini 2.5-flash (multimodal) ─► {scene, decision} ───────────────────┘   (overrides reflexes when it says so)
   │
   └─► Telegram (snapshot on "unrecognized person lingering")
```

## Hardware (all in inventory — Jetson Nano 40-pin header, BOARD pins)

| | Device | Pin / port | Role |
|---|---|---|---|
| in | Logitech C270 webcam | USB → `/dev/video0` | the "eyes" |
| out | Relay ch1 — "desk lamp" | 16 (BCM 23), active-LOW | lamp on when a person is at the desk; off after a timeout. Relay click + onboard LED is the proof (no AC load). |
| out | Relay ch2 — LED strip ("mood light") | 18 (BCM 24), active-LOW | status colour: idle / active / alert |
| out | SG90 servo — "pointer arm" | 33 (BCM 13), PWM ~50 Hz | swivels to point at the most prominent detection (1-axis pan in v1) |
| out | Passive buzzer | 32 (BCM 12), PWM tone | alert |
| disp | DWIN HDW043_001L 4.3" HDMI 480×800 cap-touch | HDMI + USB | live HUD: annotated feed, counts, FPS, Gemini's take, device states, touch overrides |

Power: Nano on a **5 V/4 A barrel** supply (`J48` jumper); SG90 on its **own 5–6 V rail**, common ground; relay coils from 5 V, logic referenced to 3.3 V. Jetson GPIO is 3.3 V.

See [docs/hardware.md](docs/hardware.md) for the authoritative pin map / wiring; `src/hardware/pins.py` mirrors it.

## Where things run

- **This repo (Windows, `D:\Projects\jetson-nano-vision-desk`)** — source of truth; authored here, pushed to GitHub (`git@github-work:futureautomate/jetson-nano-vision-desk.git`, public, **work** account).
- **Jetson Nano** — runs it: camera + `jetson-inference` + GPIO + the HDMI HUD. Reached over SSH as host `jetson` (`192.168.0.74`). Code at `~/jetson-vision-desk/`; runtime state (logs, snapshots, `.env`) at `~/jetson-vision-desk-data/`.

### Deploy / run

```powershell
./deploy.ps1            # one-way Windows → Jetson sync (rsync, else tar-over-SSH) → jetson:~/jetson-vision-desk
./deploy.ps1 -Run       # ...then run  python3 -m src.main  on the Jetson
./deploy.ps1 -Clean     # wipe ~/jetson-vision-desk first (handles deleted files)
```
```bash
./deploy.sh             # same, from Git Bash / WSL / macOS
ssh jetson 'cd ~/jetson-vision-desk && python3 -m src.main --selftest'   # exercise the actuators
```

## First-time Jetson setup

```bash
# 1. get the code there (deploy.ps1, or `git clone` the public repo)
# 2. OS + Python deps, GPIO perms:
ssh jetson 'cd ~/jetson-vision-desk && bash scripts/jetson_bootstrap.sh'
# 3. jetson-inference (NVIDIA's prebuilt Docker container handles the deps; first run downloads the models):
#    docker run --runtime nvidia -it --rm --network host -v ~/jetson-vision-desk:/work \
#      --device /dev/video0 dustynv/jetson-inference:r32.7.1
#    (or build from source: https://github.com/dusty-nv/jetson-inference)
# 4. (optional) put the Gemini key in ~/jetson-vision-desk-data/.env  (see .env.example)
```

The Nano: JetPack 4.6.x / Ubuntu 18.04 / Python 3.6 / CUDA 10.2 — on-device TensorRT vision is its strength; the Gemini call is just HTTPS so Py 3.6 is fine. (Anything needing a modern OS/Node — like NemoClaw — does **not** run on this board; that's why the companion project moved to a Pi 5.)

## Repo layout

```
deploy.ps1 / deploy.sh         # Windows ↔ Jetson sync
.env.example                   # copy → .env (gitignored): GEMINI_API_KEY, TELEGRAM_*, tuning
docs/hardware.md               # authoritative pin map + wiring
scripts/jetson_bootstrap.sh    # device setup (apt, pip, GPIO perms)
systemd/                       # kiosk autostart for the HUD
src/
  main.py                      # entry point — wires vision + reflexes + brain + HUD
  hardware/board.py            # Jetson.GPIO wrapper w/ a mock fallback (runs anywhere)
  hardware/pins.py             # the pin map (single source of truth)
  hardware/actuators.py        # relays, SG90 servo, buzzer
  vision/detector.py           # jetson-inference detectnet wrapper → detections stream
  brain/gemini.py              # periodic Gemini "what's the scene, what to do" call
  ui/hud.py                    # PyQt5 480×800 HUD on the DWIN HDMI screen
  notify/telegram.py           # alert snapshots
requirements.txt
```

## Build phases (mirrors the Notion tracker)
0. Setup — reuse the GPIO layer + deploy workflow
1. On-device vision — install `jetson-inference`, run `detectnet` on the C270, wrap it in Python
2. Reflex reactions — wire relays / servo / buzzer; person→lamp, box-centre→servo angle, alert→buzzer/LED; `--selftest`
3. PyQt5 HUD on the DWIN screen — annotated feed, counters, FPS, Gemini panel, touch overrides, boot autostart
4. Gemini brain — periodic `{scene, decision}` call, apply/override, graceful offline fallback
5. Alerts + polish — Telegram snapshot, tuning, soak test
