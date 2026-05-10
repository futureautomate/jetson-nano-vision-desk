"""Pin map for Jetson Nano Vision Desk — single source of truth (mirrors docs/hardware.md).

BOARD pin numbers (physical 40-pin header position), i.e. what Jetson.GPIO uses
after GPIO.setmode(GPIO.BOARD). BCM in parens for reference. The camera is USB
(no GPIO). Py3.6-safe (plain dicts).

v1 actuators: 2 relay channels (lamp + LED-strip mood light), 1 SG90 servo
(the pointer arm), 1 passive buzzer. PWM is software PWM via Jetson.GPIO (works
on any pin). active_low matches typical cheap relay boards — confirm on the bench.
"""

# kind: "actuator" (sensors are USB camera only in v1)
# iface: "digital_out" | "servo" | "pwm"

ACTUATORS = {
    "lamp": {
        "kind": "actuator", "iface": "digital_out", "pin": 16, "bcm": 23,
        "model": "mechanical relay ch1", "active_low": True,   # CONFIRM on bench
        "label": "Desk lamp",
        "wiring": "relay IN1 -> pin 16; module VCC 5V, GND common. No AC load (relay click + onboard LED is the proof).",
        "desc": "ON when a person is at the desk; OFF after NO_PERSON_TIMEOUT_S.",
    },
    "mood_light": {
        "kind": "actuator", "iface": "digital_out", "pin": 18, "bcm": 24,
        "model": "LED strip via mechanical relay ch2", "active_low": True,  # CONFIRM
        "label": "Status light",
        "wiring": "LED strip switched by relay IN2 -> pin 18; strip from its own supply, common ground.",
        "desc": "Status colour: idle / active / alert. (If the strip is addressable WS2812, switch to an SPI MOSI drive instead.)",
    },
    "pointer": {
        "kind": "actuator", "iface": "servo", "pin": 33, "bcm": 13,
        "model": "SG90 micro servo", "pwm_hz": 50,
        "angle_min": 0, "angle_max": 180,
        # SG90 pulse ~0.5ms (0deg) .. ~2.5ms (180deg) at 50Hz -> duty 2.5% .. 12.5%
        "duty_min": 2.5, "duty_max": 12.5,
        "label": "Pointer arm",
        "wiring": "signal -> pin 33; servo V+ from a SEPARATE 5-6V rail (not the Nano 5V pin), common ground with the Jetson.",
        "desc": "Swivels to point at the horizontal centre of the most prominent detection (1-axis pan in v1).",
    },
    "buzzer": {
        "kind": "actuator", "iface": "pwm", "pin": 32, "bcm": 12, "pwm_hz": 2000,
        "model": "5V passive buzzer",
        "label": "Buzzer",
        "wiring": "signal -> pin 32, GND common; passive buzzer needs a tone/PWM signal, not steady HIGH.",
        "desc": "Alert beeps (e.g. unrecognized person lingering).",
    },
}

# Optional inputs for later phases (not wired in v1; camera is the only sensor).
OPTIONAL_INPUTS = {
    "pir": {"kind": "sensor", "iface": "digital_in", "pin": 11, "bcm": 17, "enabled": False,
            "desc": "Optional: deep-idle the camera/GPU when nobody's around."},
}

# Spare free header pins (BOARD): 7, 12, 13, 15, 19, 21, 22, 23, 24, 26, 29, 31, 35, 36, 37, 38, 40.
# I2C-1 on pins 3/5 is free (e.g. a BMP180 if you ever want ambient data on the HUD).


def actuators():
    return dict(ACTUATORS)
