#!/bin/sh
# Install taos-demod on the handset. Run as root ON THE DEVICE.
#
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed onto the
# handset.
set -eu
HERE="$(dirname "$0")"

install -D -m 0755 "$HERE/taos-demod.py" /usr/lib/taos/taos-demod.py
install -D -m 0644 "$HERE/demo.html"     /usr/lib/taos/demo.html
install -D -m 0755 "$HERE/taos-fake-device.py" /usr/lib/taos/taos-fake-device.py
# The taOSusb Agent's lock-screen avatar, a USB stick, drawn rather than committed.
python3 "$HERE/make-usb-avatar.py" /var/lib/taos/lock-avatars/taosusb-agent.jpg >/dev/null
install -D -m 0644 "$HERE/taos-demod.service" /etc/systemd/system/taos-demod.service

# ICONS, generated from the handset's own wordmark so the PWA on Jay's other
# device carries the same mark as the boot splash. Generated rather than
# committed: one asset, no binary in the repo to drift from it.
python3 - <<'PYEOF'
from PIL import Image
from pathlib import Path
out = Path("/usr/lib/taos/demo-icons"); out.mkdir(parents=True, exist_ok=True)
mark = Image.open("/usr/share/taos/boot-wordmark.png").convert("RGBA")
for size in (192, 512):
    tile = Image.new("RGBA", (size, size), (5, 6, 10, 255))
    # 60% of the tile: a maskable icon is cropped to a circle on some
    # launchers, and anything wider than that loses its ends.
    want = int(size * 0.6)
    scaled = mark.copy()
    scaled.thumbnail((want, want), Image.LANCZOS)
    tile.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2), scaled)
    tile.convert("RGB").save(out / ("icon-%d.png" % size), "PNG")
    print("icon-%d.png" % size)
PYEOF

# SERVED OVER TAILSCALE HTTPS, not the raw port. Two reasons, both hard:
# the handset's firewall does not open 6972 to the tailnet, and a PWA needs a
# secure context before a browser will install it or register a worker.
# `tailscale serve` terminates TLS with a real certificate for the tailnet name
# and proxies to loopback, which solves both without opening anything.
tailscale serve --bg --https=443 http://127.0.0.1:6972 >/dev/null 2>&1 || \
  echo "WARNING: tailscale serve failed -- is HTTPS enabled for the tailnet?" >&2

systemctl daemon-reload
systemctl enable taos-demod.service
# RESTART, not `enable --now`. `--now` starts a stopped service and does
# nothing at all to a running one, so re-running this installer left the OLD
# code serving and the new routes answering 404 -- installed, enabled, active,
# and wrong. Same shape as any stale server answering your probe.
systemctl restart taos-demod.service

sleep 3
curl -fsS --max-time 8 http://127.0.0.1:6972/demos >/dev/null || {
  echo "taos-demod did not answer; journalctl -u taos-demod -n 40" >&2
  exit 1
}
echo "taos-demod is up on port 6972."
