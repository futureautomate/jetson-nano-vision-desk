#!/usr/bin/env bash
# Sync this repo onto the Jetson Nano over SSH (one-way: host -> Jetson).
#   ./deploy.sh            # sync only
#   ./deploy.sh --run      # sync, then run  python3 -m src.main  on the Jetson
#   ./deploy.sh --clean    # delete ~/jetson-vision-desk first, then sync
#   REMOTE=myhost ./deploy.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="${REMOTE:-jetson}"
REMOTE_DIR="~/jetson-vision-desk"
DATA_DIR="~/jetson-vision-desk-data"
EXCLUDES=(.git __pycache__ '*.pyc' .venv venv env .vscode .idea .claude data snapshots '*.log')
RUN=0; CLEAN=0
for a in "$@"; do case "$a" in --run) RUN=1;; --clean) CLEAN=1;; *) echo "unknown arg: $a" >&2; exit 2;; esac; done

echo "==> Deploying $REPO  ->  ${REMOTE}:${REMOTE_DIR}"
if command -v rsync >/dev/null 2>&1; then
  ex=(); for e in "${EXCLUDES[@]}"; do ex+=(--exclude="$e"); done
  del=(); [ "$CLEAN" -eq 1 ] && del=(--delete)
  rsync -az "${del[@]}" "${ex[@]}" "$REPO/" "${REMOTE}:${REMOTE_DIR}/"
else
  echo "    (no rsync — tar over ssh; always a clean copy)"
  ex=(); for e in "${EXCLUDES[@]}"; do ex+=(--exclude="$e"); done
  ssh "$REMOTE" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR $DATA_DIR"
  tar -czf - -C "$REPO" "${ex[@]}" . | ssh "$REMOTE" "tar -xzf - -C $REMOTE_DIR"
fi
ssh "$REMOTE" "mkdir -p $DATA_DIR"
echo "==> Synced."
if [ "$RUN" -eq 1 ]; then
  echo "==> Running  python3 -m src.main  on $REMOTE (Ctrl+C to stop)"
  ssh -t "$REMOTE" "cd $REMOTE_DIR && python3 -m src.main"
fi
