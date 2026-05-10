# Hardware — pin map & wiring (Jetson Nano Vision Desk)

> Authoritative pin map for the code. `src/hardware/pins.py` mirrors this.
> All pin numbers are **BOARD numbers** (physical 40-pin-header position) — what
> `Jetson.GPIO` uses after `GPIO.setmode(GPIO.BOARD)`. BCM in parens. Jetson GPIO is **3.3 V**.
> The camera is **USB** (no GPIO). All parts are already in the IOT inventory — buy nothing.

## Inputs
| Input | Connection | Notes |
|---|---|---|
| Logitech C270 HD webcam | USB → `/dev/video0` | the "eyes" — 720p; jetson-inference detectnet runs on this feed, and downscaled frames go to Gemini |
| *(optional, later)* PIR motion | GPIO pin 11 (BCM 17) | only if you want to deep-idle the camera/GPU when nobody's around — `enabled=False` in `pins.py` |

## Actuators
| Actuator | Pin (BOARD / BCM) | Wiring | Role |
|---|---|---|---|
| **Relay ch1 — "desk lamp"** | **16** (BCM 23), active-LOW | relay `IN1` → pin 16; module VCC 5 V, GND common. **No AC load.** | lamp ON when a person is at the desk; OFF after `NO_PERSON_TIMEOUT_S`. Relay click + onboard LED is the on-camera proof. |
| **Relay ch2 — LED strip ("mood light")** | **18** (BCM 24), active-LOW | LED strip switched by relay `IN2` → pin 18; strip from its own supply, common ground. | status colour: idle / active / alert. (If the strip turns out to be addressable WS2812, switch to an SPI-MOSI drive.) |
| **SG90 servo — "pointer arm"** | **33** (BCM 13), PWM ~50 Hz | signal → pin 33; servo V+ from a **separate 5–6 V rail** (battery pack / buck module), **common ground** with the Jetson. | swivels to point at the horizontal centre of the most prominent detection. 1-axis pan in v1 (3 SG90s in stock — add a tilt servo later). |
| **Passive buzzer** | **32** (BCM 12), PWM tone | signal → pin 32, GND common. | alert beeps (e.g. an unrecognized person lingering). |

Software PWM via `Jetson.GPIO` (works on any pin) — fine for a 50 Hz servo and a ~2 kHz buzzer; watch CPU when both run with detectnet (it's all light).

## Display
| Display | Connection | Role |
|---|---|---|
| DWIN HDW043_001L 4.3" HDMI 480×800 capacitive-touch IPS | HDMI (video) + USB (touch) | live HUD: annotated camera feed, person/object count, FPS, Gemini's scene line + decision, device states; touch buttons to pause / override (Phase 3) |

## Power
- **Jetson Nano:** 5 V / 4 A barrel-jack supply + the `J48` jumper (micro-USB browns out with camera + servo + relay drawing).
- **SG90 servo:** its own 5–6 V rail (battery pack or a buck module from inventory), **common ground** with the Nano. (One SG90 *can* run off the Nano's 5 V pin, but a separate rail avoids resets.)
- **Relay module:** coil side from 5 V; logic side referenced to the Nano's 3.3 V GPIO; use the opto-isolator jumper if the board has one.

> ⚠️ `Jetson.GPIO` has **no internal pull resistors** (it warns and ignores `pull_up_down`). Relay IN pins are actively driven by us, so it's fine; if you add the optional PIR, give it a proper 3.3 V supply and verify its OUT is ≤ 3.3 V.

## Spare header pins
BOARD 7, 12, 13, 15, 19, 21, 23, 24, 26, 29, 31, 35, 36, 37, 38, 40 are free. I²C-1 on pins 3 (SDA) / 5 (SCL) is free (e.g. a BMP180 if you ever want ambient data on the HUD). UART on pins 8/10.

## TODO before/while wiring
- [x] Bench-confirm relay polarity → active-LOW (confirmed 2026-05-10 via `--selftest`/`--demo`; `active_low: True` in `pins.py`)
- [x] Confirm the C270 enumerates as `/dev/video0` (yes — `UVC Camera (046d:0825)`, MJPEG, decoded on CPU → capture runs at 640×480 by default; see `CAMERA_WIDTH`/`HEIGHT`)
- [ ] Print/attach a pointer arm to the SG90 horn; calibrate `angle_min/angle_max` (and `duty_min/duty_max`) so the sweep matches the camera's FOV
- [ ] Wire the servo's separate 5–6 V rail with a common ground (SG90 not yet wired)
- [x] First light: `python3 -m src.main --selftest` (actuators) and `--demo` (camera → detectnet → reflexes) both verified on the Nano
