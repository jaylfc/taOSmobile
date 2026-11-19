#!/bin/sh
# Install the taOS firewall rules (50_taos.nft) and apply them to the live
# ruleset. Run as root on the handset.
#
# Never `systemctl reload nftables` here: /etc/nftables.nft starts with
# `flush ruleset`, and a reload wipes every table other daemons added at run
# time (incus NAT, docker, tailscale's ts-* chains; measured 2026-11-10, see
# pmos/incus/install-incus-net.sh). So the boot ruleset is only CHECKED with
# the new file in place, and the live change touches this file's rules alone.
# A failed check restores the previous file, so a bad rule never reaches boot.
set -eu
SRC="$(dirname "$0")/50_taos.nft"
DST=/etc/nftables.d/50_taos.nft
# Anchored to this file's comments: a bare "accept taOS " also matches the
# incus file's "accept taOS bridge from incus containers".
MARK='comment "accept taOS (web UI|device TLS) '
# A .bak left by an earlier run must never be restored over this run's file.
rm -f "$DST.bak"
[ -f "$DST" ] && cp -p "$DST" "$DST.bak"
install -m 0644 "$SRC" "$DST"
restore() {
    echo "install-firewall: $1 check FAILED, restoring the previous file" >&2
    if [ -f "$DST.bak" ]; then mv "$DST.bak" "$DST"; else rm -f "$DST"; fi
    exit 1
}
nft -c 'flush ruleset; include "/etc/nftables.nft"' || restore ruleset
# Check the live load too, before any live rule is removed.
nft -c -f "$DST" || restore live
rm -f "$DST.bak"

# Drop the live copies of this file's rules (a re-run would otherwise stack
# duplicates), then load the file.
nft -a list chain inet filter input | grep -E "$MARK" |
    sed -n 's/.*# handle \([0-9]*\)$/\1/p' | while read -r h; do
        nft delete rule inet filter input handle "$h"
    done
nft -f "$DST"

n=$(nft list chain inet filter input | grep -c -E "$MARK" || true)
if [ "$n" -ne 4 ]; then
    echo "install-firewall: expected 4 taOS rules live, found $n" >&2
    exit 1
fi
echo "install-firewall: ok"
