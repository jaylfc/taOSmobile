#!/bin/sh
# Install taos-camerad on the handset. Run as root ON THE DEVICE.
#
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed onto the
# handset, and this service is device-facing by definition.
set -eu

echo "==> packages"
# py3-libcamera is the binding; py3-pillow does the JPEG. libcamera itself is
# already on the device -- `cam` is what proved both sensors work.
apk add --no-interactive py3-libcamera py3-pillow >/dev/null

echo "==> service"
install -D -m 0755 "$(dirname "$0")/taos-camerad.py" /usr/lib/taos/taos-camerad.py
install -D -m 0644 "$(dirname "$0")/taos-camerad.service" \
  /etc/systemd/system/taos-camerad.service
install -d -o taos -g taos -m 0755 /home/taos/Pictures /home/taos/Pictures/taOS

systemctl daemon-reload
systemctl enable --now taos-camerad.service

echo "==> check"
sleep 6
curl -fsS --max-time 8 http://127.0.0.1:6971/health || {
  echo "camerad did not answer; journalctl -u taos-camerad -n 40" >&2
  exit 1
}
echo
echo "taos-camerad is up."
