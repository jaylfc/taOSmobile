#!/bin/sh
# Take the handset OUT of demo mode. Run as root ON THE DEVICE.
#
# Jay 2026-10-16: "We want the phone out of demo mode so we can start building
# out real functionality." Demo mode on the handset is three things, and this
# undoes all three:
#   1. the controller drop-in 10-lock-demo.conf (pmos/systemd/), which sets
#      every TAOS_LOCK_DEMO_* flag: invented agents, notification stacks,
#      panels, the scripted call, and the pre-auth device-agent path;
#   2. taos-demod, the demo trigger server on 6972 (install-demo.sh);
#   3. the `tailscale serve` on 443 that install-demo.sh points at 6972.
# To go back into demo mode: install the drop-in again and run install-demo.sh.
#
# The device-agent token file and the generated avatar/icons are left where
# they are: nothing reads them once the flags are gone.
set -eu

DROPIN_DIR="${TAOS_DROPIN_DIR:-/etc/systemd/system/tinyagentos.service.d}"
WAIT="${TAOS_DEMO_OFF_WAIT:-2}"

rm -f "$DROPIN_DIR/10-lock-demo.conf"
systemctl disable --now taos-demod.service || true
# `off` for this one mount only: the handset serves nothing else on 443 today,
# but a reset would also take down anything added later.
tailscale serve --https=443 off || true

systemctl daemon-reload
systemctl restart tinyagentos.service

i=0
while [ "$i" -lt 30 ]; do
    if curl -fsS --max-time 5 http://127.0.0.1:6969/api/version >/dev/null; then
        echo "demo mode is off; the controller is back on 6969."
        exit 0
    fi
    sleep "$WAIT"; i=$((i + 1))
done
echo "tinyagentos did not answer after the restart; journalctl -u tinyagentos -n 60" >&2
exit 1
