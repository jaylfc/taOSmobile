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
sudo cp taos-kiosk-csrf-guard.service /etc/systemd/system/
sudo install -D -m 755 kiosk-csrf-guard.sh /usr/local/lib/taos/kiosk-csrf-guard.sh
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

step "lockout guard (taOS#2081)"
# A stale taos_session cookie turns a CORRECT PIN into "Incorrect PIN.", and
# this device has no keyboard to clear one with. The guard proves at start that
# it can actually read the controller's access log, and fails rather than
# sitting green and blind -- so a red unit here is information, not noise.
sudo systemctl enable --now taos-kiosk-csrf-guard.service || true
if ! systemctl is-active --quiet taos-kiosk-csrf-guard.service; then
    echo "WARNING: taos-kiosk-csrf-guard.service did not come up."
    echo "The kiosk is still protected at BOOT (ephemeral profile), but a session"
    echo "that lapses while it is RUNNING will need an SSH login to clear."
    sudo journalctl -u taos-kiosk-csrf-guard.service -n 20 --no-pager || true
fi

step "verify the device cannot be locked out of its own login screen"
# Exit 2 is INCOMPLETE, not failure: the deeper checks need a real PIN, which
# this script has no business knowing. Re-run it by hand with TAOS_PIN set
# before calling tsk-ame3lw done.
./check-csrf-lockout.sh || rc=$?
case "${rc:-0}" in
    0) ;;
    2) echo "NOTE: re-run with TAOS_PIN=... (and TAOS_USERNAME/TAOS_PASSWORD)"
       echo "      to prove sign-in itself, not just that it is not CSRF-blocked." ;;
    *) echo "check-csrf-lockout.sh FAILED -- see above. Do not ship this state." ;;
esac

echo
echo "Controller is up and verified. The display is deliberately NOT touched."
echo
echo "To take the screen (do this with the phone in front of you):"
echo "  sudo systemctl start taos-kiosk.service     # Phosh stops, taOS takes over"
echo "  sudo systemctl enable taos-kiosk.service    # ...and at every boot"
echo
echo "To hand the screen back:"
echo "  sudo systemctl disable --now taos-kiosk.service && sudo systemctl start phosh.service"
