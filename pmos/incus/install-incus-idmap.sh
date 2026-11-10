#!/bin/sh
# Let incus map container root to the taOS service user, then restart incus.
# Run as root on the handset, with no agent containers running.
#
# taOS deploys set `raw.idmap "both <taos uid> 0"` so an agent's bind-mounted
# trace dir is writable. postmarketOS ships /etc/subuid and /etc/subgid with
# only root:1000000:1000000000, so lxc goes through newuidmap and refuses:
# measured 2026-11-10, deploying an agent failed with
#   newuidmap: uid range [0-1) -> [999-1000) not allowed
# "both" maps the same number for uid AND gid, so both files get the taos uid.
set -eu
ID=$(id -u taos)
for f in /etc/subuid /etc/subgid; do
    grep -qx "root:$ID:1" "$f" || echo "root:$ID:1" >> "$f"
done
systemctl restart incus
grep -x "root:$ID:1" /etc/subuid /etc/subgid
echo "install-incus-idmap: ok"
