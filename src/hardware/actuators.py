"""Actuator drivers — relays, SG90 servo, passive buzzer — via Jetson.GPIO.

Wired per src/hardware/pins.py / docs/hardware.md:
    lamp        relay ch1 = pin 16 (active-LOW)            -> relay click + onboard LED
    mood_light  relay ch2 = pin 18 (active-LOW)            -> LED strip status colour (on/off)
    pointer     SG90 servo = pin 33 (PWM 50 Hz)            -> the arm that points at things
    buzzer      pin 32 (PWM tone)

`Actuators` owns pin setup; methods are safe to call from the vision loop / HUD.
Works with or without real hardware (see src.hardware.board).
"""
import logging
import threading
import time

from src.hardware.board import gpio
from src.hardware import pins

log = logging.getLogger("vision.actuators")


def _relay_levels(active_low):
    return (0, 1) if active_low else (1, 0)   # (on_level, off_level)


class _Relay(object):
    def __init__(self, pin, active_low=True):
        self.pin = pin
        self._on_lvl, self._off_lvl = _relay_levels(active_low)
        gpio.setup_out(pin, initial=self._off_lvl)
        self._on = False

    def on(self):  gpio.write(self.pin, self._on_lvl);  self._on = True
    def off(self): gpio.write(self.pin, self._off_lvl); self._on = False
    def set(self, state): self.on() if state else self.off()
    @property
    def is_on(self): return self._on


class _Servo(object):
    """SG90 on software PWM. set_angle(0..180). A short while after each move the PWM thread
    is *stopped* (not just zeroed) — the servo relaxes (no jitter/hum) AND we don't leave a
    software-PWM thread spinning at 50 Hz stealing CPU from the vision loop."""

    def __init__(self, pin, hz=50, angle_min=0, angle_max=180, duty_min=2.5, duty_max=12.5, hold_s=0.6):
        self.pin, self.hz, self.angle_min, self.angle_max = pin, hz, angle_min, angle_max
        self.duty_min, self.duty_max, self.hold_s = duty_min, duty_max, hold_s
        self._pwm = gpio.pwm(pin, hz)
        self._running = False
        self._angle = (angle_min + angle_max) / 2.0
        self._lock = threading.Lock()
        self._detach_timer = None

    def _angle_to_duty(self, angle):
        a = max(self.angle_min, min(self.angle_max, float(angle)))
        frac = (a - self.angle_min) / float(self.angle_max - self.angle_min or 1)
        return self.duty_min + frac * (self.duty_max - self.duty_min)

    def set_angle(self, angle):
        with self._lock:
            self._angle = max(self.angle_min, min(self.angle_max, float(angle)))
            duty = self._angle_to_duty(self._angle)
            if self._running:
                self._pwm.ChangeDutyCycle(duty)
            else:
                self._pwm.start(duty)
                self._running = True
            log.debug("servo -> %.0f deg", self._angle)
            if self._detach_timer is not None:
                self._detach_timer.cancel()
            self._detach_timer = threading.Timer(self.hold_s, self._detach)
            self._detach_timer.daemon = True
            self._detach_timer.start()

    def _detach(self):
        with self._lock:
            if self._running:
                try:
                    self._pwm.stop()       # stop pulses -> servo relaxes; no lingering PWM thread
                except Exception:
                    pass
                self._running = False

    def center(self):
        self.set_angle((self.angle_min + self.angle_max) / 2.0)

    def point_at_fraction(self, frac, invert=False):
        """frac 0.0 (left edge of frame) .. 1.0 (right edge) -> a servo angle."""
        frac = max(0.0, min(1.0, float(frac)))
        if invert:
            frac = 1.0 - frac
        self.set_angle(self.angle_min + frac * (self.angle_max - self.angle_min))

    @property
    def angle(self): return self._angle

    def stop(self):
        with self._lock:
            try:
                if self._detach_timer is not None:
                    self._detach_timer.cancel()
                if self._running:
                    self._pwm.stop()
                    self._running = False
            except Exception:
                pass


class _Buzzer(object):
    """Passive buzzer driven with PWM (a square-wave tone). The PWM thread only runs while a
    beep is actually sounding — an always-on software-PWM thread at ~2 kHz wakes 2000x/sec and
    needlessly burns CPU (fighting the GIL with the vision loop), with the pin held low anyway."""

    def __init__(self, pin, default_hz=2000):
        self.pin, self.default_hz = pin, default_hz
        self._pwm = gpio.pwm(pin, default_hz)   # created, but NOT started — no idle thread
        self._lock = threading.Lock()

    def _beep_blocking(self, dur, hz):
        with self._lock:
            try:
                self._pwm.ChangeFrequency(max(50, int(hz)))
                self._pwm.start(50)
                time.sleep(dur)
            finally:
                try:
                    self._pwm.stop()
                except Exception:
                    pass

    def beep(self, dur=0.12, hz=None, blocking=False):
        hz = hz or self.default_hz
        if blocking:
            self._beep_blocking(dur, hz)
        else:
            threading.Thread(target=self._beep_blocking, args=(dur, hz), daemon=True).start()

    def alert(self):
        """Distinctive rising 3-pulse alert."""
        def run():
            for hz in (1500, 2200, 3000):
                self._beep_blocking(0.18, hz); time.sleep(0.06)
        threading.Thread(target=run, daemon=True).start()

    def off(self): pass  # nothing is held open between beeps

    def stop(self):
        try: self._pwm.stop()
        except Exception: pass


class Actuators(object):
    def __init__(self):
        a = pins.ACTUATORS
        self.lamp = _Relay(a["lamp"]["pin"], active_low=a["lamp"].get("active_low", True))
        self.mood_light = _Relay(a["mood_light"]["pin"], active_low=a["mood_light"].get("active_low", True))
        ps = a["pointer"]
        self.pointer = _Servo(ps["pin"], hz=ps.get("pwm_hz", 50),
                              angle_min=ps.get("angle_min", 0), angle_max=ps.get("angle_max", 180),
                              duty_min=ps.get("duty_min", 2.5), duty_max=ps.get("duty_max", 12.5))
        self.buzzer = _Buzzer(a["buzzer"]["pin"], default_hz=a["buzzer"].get("pwm_hz", 2000))
        self.pointer.center()

    # convenience for the reflex layer ------------------------------------
    def set_status(self, level):
        """level: 'idle' | 'active' | 'alert' -> the mood-light relay (on/off; colour is the strip's own)."""
        self.mood_light.set(level in ("active", "alert"))

    def state(self):
        return {"lamp": self.lamp.is_on, "mood_light": self.mood_light.is_on,
                "pointer_angle": round(self.pointer.angle, 1)}

    def all_off(self):
        self.lamp.off(); self.mood_light.off(); self.buzzer.off(); self.pointer.center()

    def shutdown(self):
        self.all_off()
        self.pointer.stop(); self.buzzer.stop()
