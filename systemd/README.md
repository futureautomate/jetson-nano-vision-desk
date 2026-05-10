# systemd units (Jetson autostart)

Two units:

| Unit | What it does |
|---|---|
| `jetson-vision-desk.service` | runs `python3 -m src.main --hud` — the vision/reflex loop + the PyQt5 HUD on the DWIN HDMI screen. Restarts on failure. |
| `jetson-clocks.service` | runs `jetson_clocks` on boot (pins CPU/GPU/EMC to max — it doesn't persist on its own; `nvpmodel -m 0` does and is separate). |

## Install (on the Jetson)

```bash
cd ~/jetson-vision-desk
sudo cp systemd/jetson-vision-desk.service systemd/jetson-clocks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jetson-clocks.service
sudo systemctl enable jetson-vision-desk.service     # starts on next graphical login; or:
DISPLAY=:0 sudo systemctl start jetson-vision-desk.service
```

## Notes / gotchas

- **X access:** the HUD needs the desktop session's X server. The unit sets
  `DISPLAY=:0` and `XAUTHORITY=/run/user/1000/gdm/Xauthority` (JetPack 4.6 default —
  gdm3). On a lightdm image it's usually `/home/jetson/.Xauthority` (or under
  `/var/run/lightdm/`) — check `echo $XAUTHORITY` inside the desktop session if it
  won't connect, or as a quick hack add `xhost +SI:localuser:jetson` to the session startup.
- **Notifications:** the unit's `ExecStartPre` runs `gsettings set
  org.gnome.desktop.notifications show-banners false` so desktop popups don't appear
  over the fullscreen HUD (needs `DBUS_SESSION_BUS_ADDRESS`, also set in the unit).
- **Boots to console?** Then there's no X — don't enable `jetson-vision-desk.service`;
  run `python3 -m src.main --demo` (console output) instead, or set up a minimal
  X session for the HUD.
- **Logs:** `journalctl -u jetson-vision-desk -f` (the first launch loads the TensorRT
  engine, ~3 s; cached after).
- **Edit-iterate:** after `deploy.ps1` redeploys the code, `sudo systemctl restart jetson-vision-desk`.
- **Touch is twitchy** on the cheap DWIN capacitive panel (phantom touches, esp. with the
  relay/servo wiring nearby) — keep that in mind before relying on the touch buttons; the
  keyboard shortcuts (Esc/Q/P/L/C) are steadier for testing.
