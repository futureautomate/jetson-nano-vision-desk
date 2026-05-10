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

- **X access:** the HUD needs to reach the desktop session's X server. The unit sets
  `DISPLAY=:0` and `XAUTHORITY=/home/jetson/.Xauthority`. If it can't connect
  (`could not connect to display`), check the session's actual `$XAUTHORITY` (lightdm
  often uses something under `/var/run/lightdm/`), or as a quick hack add
  `xhost +SI:localuser:jetson` to the desktop session's startup.
- **Boots to console?** Then there's no X — don't enable `jetson-vision-desk.service`;
  run `python3 -m src.main --demo` (console output) instead, or set up a minimal
  X session for the HUD.
- **Logs:** `journalctl -u jetson-vision-desk -f` (the first launch loads the TensorRT
  engine, ~3 s; cached after).
- **Edit-iterate:** after `deploy.ps1` redeploys the code, `sudo systemctl restart jetson-vision-desk`.
