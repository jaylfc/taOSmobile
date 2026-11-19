#!/bin/sh
# On the handset in taOS, as root, while no guest runs: give an already-built Omarchy guest
# (arch-root.img) the taOS agent's sudo account (guest-agent.sh), as build-arch-root.sh now does for
# a new build. Luma gets it by running stage-luma-root.sh again, which changes nothing else on a staged
# image.
set -eu
D=/var/lib/dualboot
MNT=/mnt/arch-agent
mkdir -p "$MNT"
L=$(losetup --show -f "$D/arch-root.img")
trap 'umount "$MNT" 2>/dev/null; losetup -d "$L"' EXIT
mount -t ext4 "$L" "$MNT"
. "$(dirname "$0")/guest-agent.sh"
dualboot_agent_account "$MNT" "$MNT/home/taos-agent" /home/taos-agent /home/jay/.ssh/authorized_keys
sync
echo DONE
