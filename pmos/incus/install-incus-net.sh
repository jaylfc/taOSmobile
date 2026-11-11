#!/bin/sh
# Install 52_incus.nft so taOS agent containers on incusbr0 get DHCP, DNS,
# the taOS bridge port and outbound traffic, then apply it to the live ruleset.
# Run as root on the handset.
#
# Never `systemctl reload nftables` here: /etc/nftables.nft starts with
# `flush ruleset`, and a reload wipes every table other daemons added at run
# time (incus NAT, docker, tailscale's ts-* chains). Measured 2026-11-10: one
# reload left containers with no masquerade and tailscale with no chains until
# incus, docker and tailscaled were restarted. So the boot ruleset is only
# CHECKED with the new file in place, and the live change touches this file's
# rules alone.
set -eu
SRC="$(dirname "$0")/52_incus.nft"
DST=/etc/nftables.d/52_incus.nft
MARK='incus containers'
# A .bak left by an earlier run must never be restored over this run's file.
rm -f "$DST.bak"
[ -f "$DST" ] && cp -p "$DST" "$DST.bak"
install -m 0644 "$SRC" "$DST"
restore() {
    echo "install-incus-net: $1 check FAILED, restoring the previous file" >&2
    if [ -f "$DST.bak" ]; then mv "$DST.bak" "$DST"; else rm -f "$DST"; fi
    exit 1
}
nft -c 'flush ruleset; include "/etc/nftables.nft"' || restore ruleset
# Check the live load too, before any live rule is removed: a load that
# failed after the delete loop would leave containers with no rules at all.
nft -c -f "$DST" || restore live
rm -f "$DST.bak"

# Drop the live copies of this file's inet filter rules (a re-run would
# otherwise stack duplicates), then load the file; it recreates its own table.
for chain in input forward; do
    nft -a list chain inet filter "$chain" | grep -F "$MARK" |
        sed -n 's/.*# handle \([0-9]*\)$/\1/p' | while read -r h; do
            nft delete rule inet filter "$chain" handle "$h"
        done
done
nft -f "$DST"

n=$(nft list ruleset | grep -c -F "$MARK")
if [ "$n" -ne 5 ] || ! nft list table inet taos_incus_guard >/dev/null 2>&1; then
    echo "install-incus-net: expected 5 filter rules + guard table live, found $n rules" >&2
    exit 1
fi
echo "install-incus-net: ok"
