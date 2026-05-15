# systemd units (Jetson autostart)

Two units:

| Unit | What it does |
|---|---|
| `jetson-vision-desk.service` | runs `python3 -m src.main --hud` — the identify loop + PyQt5 HUD on the DWIN screen. Restarts on failure. |
| `jetson-clocks.service` | runs `jetson_clocks` on boot (pins CPU/GPU/EMC clocks within the current `nvpmodel` budget — `nvpmodel -m 0` for 10W, `-m 1` for 5W; `jetson_clocks` doesn't persist on its own, this unit re-applies on every boot). |

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
  won't connect.
- **Wait for the WM:** the `ExecStartPre` polls for `compiz` before launching, because
  `graphical.target` reaches before the Unity/Compiz session is fully up — starting the
  HUD pre-Compiz makes the WM later decorate the window and ignore the fullscreen hint.
- **Notifications:** the unit's other `ExecStartPre` runs `gsettings set
  org.gnome.desktop.notifications show-banners false` — useful on gnome-shell images
  (no effect on this JetPack desktop, which uses `notify-osd`). The thing that *actually*
  fires the "System throttled due to Over-current" banner on this board is the
  `nvpmodel_indicator` autostart — disable it once at the kiosk user level:
  ```bash
  mkdir -p ~/.config/autostart
  cp /etc/xdg/autostart/nvpmodel_indicator.desktop ~/.config/autostart/
  printf 'Hidden=true\nX-GNOME-Autostart-enabled=false\n' >> ~/.config/autostart/nvpmodel_indicator.desktop
  ```
  (The popup is a *symptom* of a sagging 5 V rail; the proper fix is a stiffer supply / shorter barrel cable.)
- **Display stays on:** `~/.config/autostart/disable-screen-blanking.desktop` runs
  `xset s off; xset -dpms; xset s noblank` at every session login (one-time setup).
- **Boots to console?** Then there's no X — don't enable `jetson-vision-desk.service`;
  run `python3 -m src.main --demo` (console output) instead.
- **Logs:** `journalctl -u jetson-vision-desk -f` (the first launch loads the TensorRT
  engine, ~3 s; cached after).
- **Edit-iterate:** after `deploy.ps1` redeploys the code, `sudo systemctl restart jetson-vision-desk`.
- **Touch is twitchy** on the cheap DWIN capacitive panel — the keyboard shortcuts
  (`I` = identify now, `C` = clear, `Esc`/`Q` = quit) are steadier for testing.
