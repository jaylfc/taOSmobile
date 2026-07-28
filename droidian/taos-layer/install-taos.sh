#!/bin/bash
# Bring taOS up on a freshly-flashed Droidian phone.
#
# Run this ON THE DEVICE over SSH, as a user with sudo. It is idempotent:
# re-running it is safe and is the intended way to apply changes.
#
# Order matters. Everything is verified over SSH BEFORE the display is touched,
# because on this device a broken display costs a reboot to diagnose and remote
# checks cannot see boot state.
set -euo pipefail

TAOS_USER="${TAOS_USER:-$(id -un)}"
TAOS_SRC="${TAOS_SRC:-$HOME/taos-src}"
VENV="$HOME/taos-venv"
PORT=6969
STEP=0
step() { STEP=$((STEP+1)); echo; echo "=== [$STEP] $* ==="; }

step "sanity: not root, sudo works"
[ "$(id -u)" -ne 0 ] || { echo "run as a normal user, not root"; exit 1; }
sudo -n true 2>/dev/null || sudo true

step "system packages"
# Droidian is Debian: a real compiler and full apt, so none of the
# aarch64-wheel gymnastics the Ubuntu Touch install needed.
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3-venv python3-dev build-essential \
    cage chromium \
    git curl rsync

step "python venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip wheel

step "taOS controller"
if [ -d "$TAOS_SRC" ]; then
    # --prefer-binary, not --only-binary: http-ece is source-only but pure
    # Python, and --only-binary refuses the whole resolve because of it.
    "$VENV/bin/pip" install --prefer-binary -e "$TAOS_SRC"
else
    echo "taOS source not found at $TAOS_SRC"
    echo "rsync it from the workstation first, then re-run:"
    echo "  rsync -az --exclude desktop/ --exclude .git/ tinyagentos/ droidian:~/taos-src/"
    exit 1
fi

step "static assets present?"
# taOS#2080: static/ is not shipped as package data, so a non-editable install
# serves no SPA. The editable install above keeps SPA_DIR pointing at the
# source tree, which is why it is -e and not a plain install.
"$VENV/bin/python" - <<'PY'
import sys
from tinyagentos.routes import desktop
ok = (desktop.SPA_DIR / "index.html").is_file()
print(f"SPA_DIR={desktop.SPA_DIR} index={'present' if ok else 'MISSING'}")
sys.exit(0 if ok else 1)
PY

step "install units"
sed "s/%i/$TAOS_USER/g; s/%U/$(id -u "$TAOS_USER")/g" taos-controller.service \
    | sudo tee /etc/systemd/system/taos-controller.service >/dev/null
sed "s/%i/$TAOS_USER/g; s/%U/$(id -u "$TAOS_USER")/g" taos-kiosk.service \
    | sudo tee /etc/systemd/system/taos-kiosk.service >/dev/null
sudo cp taos-kiosk-recover.service /etc/systemd/system/
sudo systemctl daemon-reload

step "start the controller and WAIT for the port"
sudo systemctl enable --now taos-controller.service
waited=0
until (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do
    [ "$waited" -ge 120 ] && { echo "controller never opened :$PORT"; \
        sudo journalctl -u taos-controller.service -n 40 --no-pager; exit 1; }
    sleep 3; waited=$((waited+3))
done
exec 3<&- || true
echo "controller listening on :$PORT after ${waited}s"

echo
echo "Controller is up and verified. The display is deliberately NOT touched."
echo
echo "To take the screen (do this with the phone in front of you):"
echo "  sudo systemctl start taos-kiosk.service     # Phosh stops, taOS takes over"
echo "  sudo systemctl enable taos-kiosk.service    # ...and at every boot"
echo
echo "To hand the screen back:"
echo "  sudo systemctl disable --now taos-kiosk.service && sudo systemctl start phosh.service"
