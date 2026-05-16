# 👁️ Jetson Nano Vision Desk — Object Identifier

A Jetson Nano with a webcam and a small HDMI screen. Hold an object up to the camera — a book, a phone, a coffee bag, a Lego brick, a tool, a banknote — and the Nano figures out *what it is* and shows you the details on the screen. **Fast on-device detection triggers the call; a cloud multimodal AI does the recognition.**

---

## How it works — two layers, one job

**Layer 1 — on-device, watching:** `jetson-inference` `detectnet` (SSD-Mobilenet-v2, **prebuilt — zero training**) runs on the Nano's 128-core GPU and continuously labels what it sees ("book", "cell phone", "bottle", "cup", ...). Its only role is to *trigger* a recognition when an object's been steady in frame for ~2 seconds. Works offline; no cloud needed for this part.

**Layer 2 — cloud, identifying:** the moment Layer 1 trips a trigger, a downscaled frame + the detector's hint label go to **Google Gemini 2.5-flash** (multimodal — a plain HTTPS call, fine on the Nano's Python 3.6). Gemini returns a structured **`{name, kind, summary, facts[]}`** JSON the HUD renders into a tidy "card" — name big at the top, a `BOOK` / `PRODUCT` / `ELECTRONICS` / ... chip, a one-line summary, and 3–5 specific facts (author + year for a book, brand + specs for a product, calories + origin for food, …).

```
USB webcam (C270)
   │
   ▼
jetson-inference detectnet  ─►  stable detection for ~2 s  ─┐
                                                            │ (async — never stalls the HUD)
                                                            ▼
                          Gemini 2.5-flash (multimodal)  ──►  {name, kind, summary, facts}
                                                            │
   ┌────────────────────────────────────────────────────────┘
   ▼
PyQt5 HUD on the DWIN HDMI screen  ──►  Telegram (snapshot + caption per scan)
(live feed left, identification card right; Identify Now / Clear buttons)
```

State machine: `IDLE → WATCHING → IDENTIFYING → SHOWING → IDLE` (when the object leaves frame for `CLEAR_S` seconds). A new, different object in frame restarts the watch. The `I` key (or the touch button) forces an identification right now.

## Hardware — just these three things

| Component | How it connects | Role |
|---|---|---|
| **NVIDIA Jetson Nano B01** (4 GB) | the board | runs the loop, hosts the HUD |
| **Logitech C270 webcam** | USB → `/dev/video0` | 640×480 raw YUY2 — detectnet downscales to 300×300 |
| **DWIN HDW043_001L 4.3" HDMI** | HDMI (video) + USB (touch) | fullscreen kiosk HUD with two touch buttons |

5 V supply into the Nano's barrel jack (`J48` jumper fitted). No GPIO, no relays, no servos, no level shifters. See [docs/hardware.md](docs/hardware.md).

## Where things run

**Everything runs on the Nano.** Code lives at `~/jetson-vision-desk/`, runtime state (logs, snapshots, `.env`) at `~/jetson-vision-desk-data/`. The HUD renders on whatever HDMI display is attached to the Nano's HDMI port. There is no host-side runtime — your laptop doesn't need to be in the loop once the Nano is set up.

The setup below clones the repo directly on the Nano. If you'd rather edit on your laptop and sync changes over SSH, see **"Optional: develop from a dev machine"** further down.

## First-time Jetson setup

All commands run **on the Jetson** (over SSH or directly at the desktop):

```bash
# 1. clone the repo onto the Nano
git clone https://github.com/futureautomate/jetson-nano-vision-desk.git ~/jetson-vision-desk
cd ~/jetson-vision-desk

# 2. OS + Python deps (apt + pip)
bash scripts/jetson_bootstrap.sh

# 3. build jetson-inference FROM SOURCE — installs the `jetson.inference` / `jetson.utils`
#    Python bindings system-wide, so the app runs natively (HUD + systemd stay simple).
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

# 4. fill in your keys (the bootstrap in step 2 already created this from .env.example)
#       GEMINI_API_KEY=...                      (required for actual identifications)
#       GEMINI_MODEL=gemini-2.5-flash
#       TELEGRAM_BOT_TOKEN=...                  (optional — snapshot per scan to your phone)
#       TELEGRAM_CHAT_ID=...                    (optional)
nano ~/jetson-vision-desk-data/.env

# 5. smoke test — runs the identify loop with console output (no HUD)
cd ~/jetson-vision-desk
python3 -m src.main --demo

# 6. install + enable systemd units so the HUD autostarts at boot (see systemd/README.md)
sudo cp ~/jetson-vision-desk/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jetson-clocks jetson-vision-desk

# 7. (optional) full-speed perf — only if your 5V rail is stiff (≥ 4.9V at the barrel jack
#    under load); otherwise leave the default 5W mode.
sudo nvpmodel -m 0 && sudo jetson_clocks
```

The Nano: JetPack 4.6.x / Ubuntu 18.04 / Python 3.6 / CUDA 10.2 — on-device TensorRT vision is its strength; the Gemini call is plain HTTPS so Py 3.6 is fine.

### Run modes

```bash
python3 -m src.main           # prints config + exits (sanity check)
python3 -m src.main --demo    # identify loop, console output (good for headless / SSH)
python3 -m src.main --hud     # ...with the PyQt5 HUD on the attached HDMI display
```

`--hud` needs an X session — if you boot to console, stick with `--demo`, or use the systemd unit from step 6 (it handles `DISPLAY` / `XAUTHORITY` for you).

## Repo layout

```
deploy.ps1 / deploy.sh         # OPTIONAL — dev-machine → Jetson sync (see below)
.env.example                   # copy → .env (gitignored): GEMINI_API_KEY, TELEGRAM_*, tuning
docs/hardware.md               # the three components
scripts/jetson_bootstrap.sh    # device setup (apt, pip)
systemd/                       # autostart units (HUD + jetson_clocks) — see systemd/README.md
src/
  main.py                      # entry point — --demo / --hud
  engine.py                    # the identify state machine (IDLE → WATCHING → IDENTIFYING → SHOWING)
  vision/detector.py           # jetson-inference detectnet wrapper → detections stream
  brain/gemini.py              # Gemini "identify this" call — returns {name, kind, summary, facts}
  ui/hud.py                    # PyQt5 HUD for the DWIN HDMI screen (landscape/portrait adaptive)
  notify/telegram.py           # per-scan snapshot + caption to your Telegram bot (async, cooldown-debounced)
requirements.txt
```

## Optional: develop from a dev machine

If you'd rather edit code in your usual IDE on Windows / Linux / macOS and push changes over SSH (instead of editing on the Nano directly), use the included deploy scripts. Everything else — the bootstrap, `jetson-inference` build, `.env`, systemd units — still runs on the Nano exactly as in the "First-time Jetson setup" above.

### 1. SSH alias on your dev machine

Add this to `~/.ssh/config` (or `%USERPROFILE%\.ssh\config` on Windows) — replace `JETSON_IP_OR_HOSTNAME` and `USER`:

```sshconfig
Host jetson
    HostName JETSON_IP_OR_HOSTNAME
    User USER
    IdentityFile ~/.ssh/id_ed25519
```

Then `ssh-copy-id jetson` once for passwordless login.

### 2. Sync the repo to the Nano

```powershell
./deploy.ps1            # one-way sync (rsync if available, else tar-over-SSH) → jetson:~/jetson-vision-desk
./deploy.ps1 -Run       # ...then run python3 -m src.main on the Jetson
./deploy.ps1 -Clean     # wipe ~/jetson-vision-desk first (handles deleted files)
./deploy.ps1 -RemoteHost myhost   # override the SSH host alias
```

```bash
./deploy.sh             # same, from Git Bash / WSL / Linux / macOS
./deploy.sh --run
./deploy.sh --clean
REMOTE=myhost ./deploy.sh
```

### 3. Run / restart on the Nano after a sync

```bash
ssh jetson 'cd ~/jetson-vision-desk && python3 -m src.main --demo'        # console output
ssh jetson 'sudo systemctl restart jetson-vision-desk'                    # if the service is installed
```

After the first sync, finish the rest of "First-time Jetson setup" on the Nano (bootstrap, `jetson-inference` build, `.env`, systemd units). Subsequent edits are just `./deploy.ps1` (or `./deploy.sh`) followed by a service restart.

## Performance

Measured on the actual board (Nano B01, JetPack R32.7.6 / TRT 8.2.1):

- **Default boot is 5 W mode** (`nvpmodel -m 1`) — keeps current draw within what a marginal 5 V/4 A supply can deliver. detectnet still clocks **~15–16 FPS** in 5 W mode because the OC throttle isn't dynamically clamping (counter-intuitive but real — in MAXN on a sagging rail the SoC silently drops the GPU clock).
- **Switch to MAXN** (`sudo nvpmodel -m 0 && sudo jetson_clocks`) only when the supply is genuinely stiff — confirm ≥ 4.9 V at the Nano's barrel jack while detectnet is running. If you see `soctherm: OC ALARM` repeatedly in `dmesg`, you're not there yet.
- **Camera:** at ≤ 640×480 jetson-utils picks the C270's **raw YUY2** stream (no CPU JPEG decode); detectnet downscales to 300×300 regardless.
- **Gemini call:** runs in a side thread, so a slow (or quota-exhausted, or offline) Gemini doesn't freeze the live feed. Typical identification: ~1–3 s end-to-end.
- **Gemini free-tier quota** runs out in a few hours under continuous use — enable billing on the AI Studio project for sustained operation, or just live with the occasional `429`s (the engine returns to IDLE and lets you retry).

## Build phases — status

0. ✅ Setup — repo, deploy workflow
1. ✅ On-device vision — `jetson-inference` built from source; `detectnet` running on the C270 wrapped in `src/vision/detector.py`
2. ✅ Engine — identify state machine in `src/engine.py` (replaces the original reflex layer)
3. ✅ PyQt5 HUD on the DWIN screen — `src/ui/hud.py`: live feed + identification card + `IDENTIFY NOW` / `CLEAR` buttons + keyboard shortcuts; `systemd/` units installed & enabled; fullscreen at autostart (Compiz-wait fix); notification popups silenced; display-stays-on configured
4. ✅ Gemini brain — async `identify()` call; structured `{name, kind, summary, facts}` JSON; graceful fallback on no key / network / quota
5. ✅ Telegram — each identification logs a snapshot + caption to your bot, async, de-duped, cooldown-limited

(Previous reflex/relay/servo/buzzer build was removed in this pivot — see git history if you want the original Vision Desk variant.)
