# 👁️ Jetson Nano Vision Desk

An NVIDIA **Jetson Nano** with a webcam that watches your desk, sees who/what's there in **real time on its own GPU**, *physically* runs the room (desk lamp, a servo "pointer" arm, a buzzer, an LED strip), and — every few seconds — hands a frame to a **multimodal AI (Google Gemini)** that reasons about the whole scene and decides what to do. **Local model = fast reflexes; cloud VLM = the thinking layer.** Every part is already in the IOT inventory — **buy nothing**.

> Project brief & build phases: Notion → *Future Automation — Mission Control › Project Tracker › "Jetson Nano Vision Desk"*.
> This project re-uses the GPIO layer + deploy workflow from the (re-homed) [`Jetson-Nano-NeMo-Claw-Iot-Companion`](https://github.com/futureautomate/Jetson-Nano-NeMo-Claw-Iot-Companion) repo.

---

## How it works — two layers

**Layer 1 — on-device vision (works offline):** `jetson-inference` `detectnet` (SSD-Mobilenet-v2, **prebuilt — zero training**) runs on the Nano's 128-core GPU → bounding boxes + labels each frame → GPIO reactions (lamp on when a person's at the desk, the servo arm tracks the most prominent object, buzzer/LED on alert). On JetPack 4.6.3 / TensorRT 8.2.1 the prebuilt UFF model clocks ~4 FPS (the often-quoted ~20–30 FPS was on older JetPack/TRT) — plenty for "is a person here / point at the prominent thing" reflexes; see [Performance](#performance).

**Layer 2 — cloud "brain" (periodic, reasons about the scene):** every N seconds a downscaled frame + the detections go to **Gemini 2.5-flash** (multimodal, a plain HTTPS call — fine on the Nano's Python 3.6) → it returns a one-line **scene description** + a structured **action decision** (`{scene, lamp, point_at, alert, status, say}`). It's *advisory* — it can add to the reflexes (and owns the servo target / status LED / alerts) but never undoes "person → lamp on"; the call is async so the vision loop never stalls; and it falls back to pure local reflexes if the network/API is down or there's no key. (Note: the Gemini free tier's daily quota runs out under an 8 s interval — enable billing on the AI Studio project, or raise `GEMINI_INTERVAL_S`, for sustained use.)

```
USB webcam (C270)
   │
   ▼
jetson-inference detectnet ─► detections ─► REFLEX rules ─► GPIO (lamp / servo / buzzer / LED)
   │                              │                                 ▲
   │ (every N s: frame+dets)       └─────────────────────────────────┼──► PyQt5 HUD on the DWIN HDMI screen
   ▼                              │                                 │
Gemini 2.5-flash (multimodal) ─► {scene, decision} ───────────────────┘   (advisory overlay on the reflexes)
   │
   └─► Telegram ── snapshot when a person arrives ("N person(s) at the desk — lamp on")
                └─ snapshot on a Gemini alert ("unfamiliar person lingering" …)
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
ssh jetson 'cd ~/jetson-vision-desk && python3 -m src.main --demo'       # vision → reflex loop, console output
ssh jetson 'cd ~/jetson-vision-desk && DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority python3 -m src.main --hud'  # ...with the HUD on the DWIN screen
```

## First-time Jetson setup

```bash
# 1. get the code there (deploy.ps1, or `git clone` the public repo)

# 2. OS + Python deps, GPIO perms:
ssh jetson 'cd ~/jetson-vision-desk && bash scripts/jetson_bootstrap.sh'
#    (log out / back in once so the gpio/video groups take effect)

# 3. build jetson-inference FROM SOURCE — this installs the `jetson.inference` / `jetson.utils`
#    Python bindings system-wide, so the app runs natively (GPIO + the Qt HUD + systemd all stay
#    simple — a container would force all of that inside it). On the Jetson:
git clone --depth=1 https://github.com/dusty-nv/jetson-inference ~/jetson-inference
cd ~/jetson-inference
git submodule update --init --recursive utils tools/camera-capture c/plugins/pose
#    JetPack 4.x quirk: the apt numpy hides libnpymath.a from the linker — expose it first:
sudo ln -sf /usr/lib/python3/dist-packages/numpy/core/lib/libnpymath.a /usr/lib/aarch64-linux-gnu/libnpymath.a
mkdir build && cd build
cmake -DBUILD_INTERACTIVE=NO ../          # downloads the default models, skips PyTorch, no dialogs
make -j2 && sudo make install && sudo ldconfig
python3 -c 'import jetson.inference, jetson.utils; print("bindings ok")'
#    (first detectNet(...) call downloads SSD-Mobilenet-v2 + builds a TensorRT engine, ~5 min, cached after)
#    (Docker alternative: dustynv/jetson-inference:r32.7.1 — but then GPIO/HUD/systemd all have to run inside it)

# 4. (optional) put the Gemini key in ~/jetson-vision-desk-data/.env  (see .env.example)

# 5. perf tip: the Nano boots in 5W mode, which caps detectnet FPS — `sudo nvpmodel -m 0 && sudo jetson_clocks`
```

The Nano: JetPack 4.6.x / Ubuntu 18.04 / Python 3.6 / CUDA 10.2 — on-device TensorRT vision is its strength; the Gemini call is just HTTPS so Py 3.6 is fine. (Anything needing a modern OS/Node — like NemoClaw — does **not** run on this board; that's why the companion project moved to a Pi 5.)

## Repo layout

```
deploy.ps1 / deploy.sh         # Windows ↔ Jetson sync
.env.example                   # copy → .env (gitignored): GEMINI_API_KEY, TELEGRAM_*, tuning
docs/hardware.md               # authoritative pin map + wiring
scripts/jetson_bootstrap.sh    # device setup (apt, pip, GPIO perms)
systemd/                       # autostart units (HUD + jetson_clocks) — see systemd/README.md
src/
  main.py                      # entry point — --selftest / --demo / --hud
  engine.py                    # the vision→reflex(→Gemini) loop, decoupled from UI; yields a Snapshot/frame
  hardware/board.py            # Jetson.GPIO wrapper w/ a mock fallback (runs anywhere)
  hardware/pins.py             # the pin map (single source of truth)
  hardware/actuators.py        # relays, SG90 servo, buzzer (lazy PWM)
  vision/detector.py           # jetson-inference detectnet wrapper → detections stream
  brain/gemini.py              # periodic Gemini "what's the scene, what to do" call
  ui/hud.py                    # PyQt5 HUD for the DWIN HDMI screen (landscape/portrait adaptive)
  notify/telegram.py           # Telegram snapshots — person arrives + Gemini alert (async, separate cooldowns; dormant w/o a token)
requirements.txt
```

## Performance

Measured on the actual board (Nano B01, JetPack R32.7.6 / Ubuntu 18.04 / TRT 8.2.1, `nvpmodel -m 0` + `jetson_clocks`):

- **detectnet ~4 FPS** with the prebuilt UFF SSD-Mobilenet-v1/v2/-Inception-v2 — all ~4 FPS; `Detect()` is ~250 ms of *TensorRT inference* (pre/post-process are <1 ms, the camera isn't the bottleneck). The "~20–30 FPS" in dusty-nv's docs was on JetPack 4.4–4.5 / TRT 7.x; TRT 8.2's UFF path is much slower on the Nano. 4 FPS is fine for the reflex layer (lamp/servo/alert) and the Gemini call is every N s anyway. If you ever need more: INT8 calibration (~2×, but needs a calib set), or a person-only DetectNet model (`pednet`/`multiped` — fast, but no 91-class COCO).
- **Camera:** at ≤640×480 jetson-utils picks the C270's **raw YUY2** stream (no CPU JPEG decode); at 720p+ it falls back to MJPEG + a CPU `jpegdec`. Default capture is 640×480 (`CAMERA_WIDTH`/`CAMERA_HEIGHT`) — detectnet downscales to 300×300 regardless.
- **Software PWM:** the buzzer (~2 kHz) and servo (50 Hz) PWM threads only run while actually beeping / moving — an always-on software-PWM thread needlessly burns CPU on the Nano.
- `sudo nvpmodel -m 0` persists across reboots; `sudo jetson_clocks` does not — re-run it each boot (`systemd/jetson-clocks.service` does this).
- **Power:** if the desktop pops *"System throttled due to Over-current"*, the 5 V rail is sagging — use the **5 V/4 A barrel jack + the `J48` jumper**, not micro-USB. Throttling also drags inference FPS down further.

## Status

All five build phases are **done in code** and the whole stack runs on the Nano via systemd autostart — `jetson-vision-desk.service` (the HUD + the vision/reflex/Gemini/Telegram loop on the DWIN screen) and `jetson-clocks.service` (pins max clocks on boot); the over-current desktop popup is disabled (see `systemd/README.md`). What's left is hardware/ops on the bench:

- **Power** — move to the 5 V/4 A barrel jack + the `J48` jumper (the over-current is real; it's the main thing capping FPS and a hang risk).
- **Wire the SG90** (pin 33 — its own 5–6 V rail, common ground) and the **buzzer** (pin 32), then calibrate `angle_min/max` & `duty_min/max` in `src/hardware/pins.py` to the camera's FOV. *(For a quick demo without a separate rail you can run the servo off the Nano's 5 V pin, but with the board already over-current that risks a brownout — dry-run `--selftest` first.)*
- **Optional:** a Telegram bot token in `.env` to enable the alerts; tuning (`DETECT_CONFIDENCE` / `NO_PERSON_TIMEOUT_S` / the Gemini prompt, all via `.env`); a soak run; tame the twitchy capacitive touch; exercise `deploy.ps1` (so far the code's been `scp`'d).

## Build phases (mirrors the Notion tracker)
0. ✅ Setup — GPIO layer + deploy workflow (reused from the companion repo)
1. ✅ On-device vision — `jetson-inference` built from source, `detectnet` (SSD-Mobilenet-v2) on the C270, wrapped in `src/vision/detector.py`
2. ✅ Reflex reactions — person→lamp (with a `NO_PERSON_TIMEOUT_S` grace period), box-centre→servo angle, status LED, alert→buzzer; `--selftest`/`--demo`; perf-tuned. *(Verified on the relays; SG90 + buzzer not yet physically wired.)*
3. ✅ PyQt5 HUD on the DWIN screen — `src/ui/hud.py`: annotated feed, person/object counts, FPS, Gemini scene/decision panel, touch buttons (pause / lamp AUTO·ON·OFF / center); runs via `--hud`; `systemd/` units installed & enabled; notification popup silenced. *(Twitchy touch still a minor wart.)*
4. ✅ Gemini brain — async `{scene, decision}` call every N s, advisory overlay on the reflexes (reflex stays the *floor*), graceful offline/keyless/quota fallback
5. ✅ Alerts — Telegram snapshot on a Gemini `alert` **and** when a person arrives (`src/notify/telegram.py`, async, separate cooldowns; drop `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env` to enable). *Polish/tuning/soak ongoing.*
