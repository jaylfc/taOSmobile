#!/bin/sh
# Install the taOS firewall rules (50_taos.nft) and reload nftables.
# Run as root on the handset. The ruleset is syntax-checked with the new file
# in place BEFORE the reload, and the old file is restored if the check fails,
# so a bad rule can never take down ssh (port 22) with it.
set -eu
SRC="$(dirname "$0")/50_taos.nft"
DST=/etc/nftables.d/50_taos.nft
[ -f "$DST" ] && cp -p "$DST" "$DST.bak"
install -m 0644 "$SRC" "$DST"
if ! nft -c 'flush ruleset; include "/etc/nftables.nft"'; then
    echo "install-firewall: ruleset check FAILED, restoring the previous file" >&2
    if [ -f "$DST.bak" ]; then mv "$DST.bak" "$DST"; else rm -f "$DST"; fi
    exit 1
fi
systemctl reload nftables 2>/dev/null || systemctl restart nftables
nft list chain inet filter input | grep -E 'dport (6969|6974)'
