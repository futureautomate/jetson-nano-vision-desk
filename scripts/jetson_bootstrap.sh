#!/usr/bin/env bash
# One-shot device setup for the Jetson Nano side of Vision Desk.
# Run it FROM the deployed repo on the Jetson:
#     ssh jetson 'cd ~/jetson-vision-desk && bash scripts/jetson_bootstrap.sh'
# Safe to re-run. It does NOT install jetson-inference (see the note at the end).
set -euo pipefail

echo "==> Jetson bootstrap — $(uname -m), $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
[ -f /etc/nv_tegra_release ] && echo "    $(cat /etc/nv_tegra_release)"
echo "    python3: $(python3 --version 2>&1)"

DATA_DIR="${HOME}/jetson-vision-desk-data"
mkdir -p "$DATA_DIR"
[ -f "$DATA_DIR/.env" ] || { [ -f "$(dirname "$0")/../.env.example" ] && cp "$(dirname "$0")/../.env.example" "$DATA_DIR/.env" && echo "==> created $DATA_DIR/.env from .env.example — fill in your keys"; }
echo "==> runtime data dir: $DATA_DIR"

echo "==> apt packages (HUD + OpenCV for the JPEG path + small utilities)"
APT_PKGS=(python3-pip python3-dev python3-setuptools python3-pyqt5 python3-opencv git curl)
sudo apt-get update -y
if ! sudo apt-get install -y "${APT_PKGS[@]}"; then
  echo "    (group install failed — retrying individually)"
  for p in "${APT_PKGS[@]}"; do sudo apt-get install -y "$p" || echo "    !! skipped: $p"; done
fi

echo "==> python pip deps (user install)"
python3 -m pip install --user --upgrade pip || true
python3 -m pip install --user requests || true

echo
echo "==> Done. Next steps:"
echo "    * Build jetson-inference FROM SOURCE (installs the jetson.inference/jetson.utils Python"
echo "      bindings system-wide, so the app runs natively — see README 'First-time Jetson setup'):"
echo "        git clone --depth=1 https://github.com/dusty-nv/jetson-inference ~/jetson-inference"
echo "        cd ~/jetson-inference && git submodule update --init --recursive utils tools/camera-capture c/plugins/pose"
echo "        sudo ln -sf /usr/lib/python3/dist-packages/numpy/core/lib/libnpymath.a /usr/lib/aarch64-linux-gnu/libnpymath.a  # JetPack-4 npymath quirk"
echo "        mkdir build && cd build && cmake -DBUILD_INTERACTIVE=NO ../ && make -j2 && sudo make install && sudo ldconfig"
echo "    * Put your Gemini key in $DATA_DIR/.env  (GEMINI_API_KEY=..., GEMINI_MODEL=gemini-2.5-flash)"
echo "    * Optional: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID for an identification log to your phone"
echo "    * Quick checks:"
echo "        ls /dev/video0                    # the C270 should be there"
echo "        python3 -m src.main               # prints config"
echo "        python3 -m src.main --demo        # camera -> detectnet -> Gemini identify (console)"
echo "        python3 -m src.main --hud         # ...with the HUD on the DWIN screen"
echo "    * Perf: the Nano boots in 5W mode by default — 'sudo nvpmodel -m 0 && sudo jetson_clocks' for full speed,"
echo "      but only if your 5V supply doesn't sag under load (see README's Performance section)."
