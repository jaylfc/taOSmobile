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
    if ! grep -qx "root:$ID:1" "$f"; then
        # A file without a final newline would glue the entry onto its last
        # line and break the existing root range.
        [ -s "$f" ] && [ -n "$(tail -c 1 "$f")" ] && echo >> "$f"
        echo "root:$ID:1" >> "$f"
    fi
    # Checked per file, before the restart: one grep over both files passes
    # when only one of them has the line.
    grep -qx "root:$ID:1" "$f" || { echo "install-incus-idmap: $f lacks root:$ID:1" >&2; exit 1; }
done
systemctl restart incus
echo "install-incus-idmap: ok"
