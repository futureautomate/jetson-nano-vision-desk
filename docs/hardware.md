# Hardware — Jetson Nano Vision Desk (Object Identifier)

Pure software project — no GPIO, no sensors, no actuators. Just three things plugged into the Nano.

| Component | How it connects | Role |
|---|---|---|
| **NVIDIA Jetson Nano B01** | the board itself | the brain — runs `jetson-inference` `detectnet` on its 128-core GPU, talks to Gemini over HTTPS |
| **Logitech C270 webcam** | USB → `/dev/video0` | the eyes — 640×480 raw YUY2 stream (no CPU JPEG decode at this res); detectnet downscales to 300×300 |
| **DWIN HDW043_001L 4.3" HDMI** | HDMI (video) + USB (touch) | the screen — fullscreen PyQt5 HUD: live feed + identification card + `IDENTIFY NOW` / `CLEAR` buttons |

## Power

- 5 V supply into the Nano's **barrel jack** (`J48` jumper fitted). 5 V/2 A is plenty for this config (no GPIO loads); 5 V/4 A gives headroom if you ever go back to `nvpmodel -m 0` (MAXN).
- The DWIN screen takes its own USB 5 V (any USB port); HDMI carries only video.
- The Nano boots in 5 W mode by default (`/etc/nvpmodel.conf` → `DEFAULT=1` after the kiosk setup). Switch to MAXN with `sudo nvpmodel -m 0` if the supply is genuinely stiff (≥ 4.9 V at the barrel jack under load) — otherwise the `soctherm` OC alarm trips and throttles, which actually hurts inference latency.

## That's it

No 40-pin header wiring, no relays, no servos, no buzzers. If you ever want to add visual feedback (e.g. an LED strip pulsing while it's "thinking"), wire it independently — the project doesn't drive any GPIO.
