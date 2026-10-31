#!/bin/sh
# Install Waydroid on the handset: Android apps as windows in the taOS session.
#
# Run as root ON the device. Idempotent and RESUMABLE: the Android image is a
# ~1 GB download that outlives any ssh session because it runs in a transient
# systemd unit (waydroid-init); re-running this script after a dropped
# connection picks up where it was.
#
# What it installs, in order, and why that order:
#   1. the packages (waydroid pulls waydroid-systemd, whose unit this drops in on)
#   2. the handset check/harden script, the session helper, the lxc lines
#   3. the unit drop-in (ExecStartPre check, a real ExecStop)
#   4. the firewall file, BEFORE the first container start: /etc/nftables.nft
#      begins with `flush ruleset`, so a firewall reload wipes the nft tables
#      waydroid-net.sh adds at start. Firewall first, container second, and any
#      later firewall reload needs `systemctl restart waydroid-container`.
#   5. `waydroid init` (the download), unless already initialised
#   6. enable + start the container manager; the drop-in's check runs as
#      ExecStartPre. The LXC container only starts with the first session
#      (taos-waydroid-session), which also sets the multi-window property.
#
# WAYDROID_SYSTEM_TYPE=VANILLA|GAPPS picks the image on first init (default
# VANILLA, Jay 2026-10-31). Changing it later: `waydroid init -f -s GAPPS`.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SYSTEM_TYPE="${WAYDROID_SYSTEM_TYPE:-VANILLA}"
WORK=/var/lib/waydroid
UNIT=waydroid-container.service
DROPIN_DIR=/etc/systemd/system/$UNIT.d
NFT_SRC="$HERE/51_waydroid.nft"
NFT_DST=/etc/nftables.d/51_waydroid.nft

[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
for f in bin/taos-waydroid-check bin/taos-waydroid-session lxc-taos.conf \
         systemd/waydroid-container.service.d/10-taos.conf 51_waydroid.nft; do
    [ -r "$HERE/$f" ] || { echo "FAIL: $HERE/$f not readable"; exit 1; }
done
case "$SYSTEM_TYPE" in VANILLA|GAPPS) ;; *) echo "FAIL: WAYDROID_SYSTEM_TYPE must be VANILLA or GAPPS, got '$SYSTEM_TYPE'"; exit 1 ;; esac

# 1. Packages. waydroid-systemd is a separate subpackage; the drop-in below is
#    useless without the unit it extends, so check the unit file, not the apk.
apk add --quiet waydroid waydroid-systemd
[ -f /usr/lib/systemd/system/$UNIT ] || { echo "FAIL: /usr/lib/systemd/system/$UNIT not installed by waydroid-systemd"; exit 1; }
echo "PASS: waydroid $(apk version waydroid 2>/dev/null | awk 'NR==2{print $1}' | sed 's/^waydroid-//') + unit installed"

# 2. Handset scripts and the lxc lines the check appends.
install -m 755 "$HERE/bin/taos-waydroid-check" /usr/local/bin/taos-waydroid-check
install -m 755 "$HERE/bin/taos-waydroid-session" /usr/local/bin/taos-waydroid-session
install -d -m 755 /etc/taos/waydroid
install -m 644 "$HERE/lxc-taos.conf" /etc/taos/waydroid/lxc-taos.conf
echo "installed /usr/local/bin/taos-waydroid-check, /usr/local/bin/taos-waydroid-session, /etc/taos/waydroid/lxc-taos.conf"

# 3. The drop-in. Only systemd's own list of loaded drop-ins proves it is in
#    effect; a unit that merely starts does not.
install -d -m 755 "$DROPIN_DIR"
install -m 644 "$HERE/systemd/waydroid-container.service.d/10-taos.conf" "$DROPIN_DIR/10-taos.conf"
systemctl daemon-reload
systemctl show -p DropInPaths $UNIT | grep -q "$DROPIN_DIR/10-taos.conf" \
    && echo "PASS: 10-taos.conf is loaded for $UNIT" \
    || { echo "FAIL: 10-taos.conf is not among the unit's drop-ins"; exit 1; }
systemctl show -p ExecStartPre $UNIT | grep -q taos-waydroid-check \
    && echo "PASS: ExecStartPre runs taos-waydroid-check" \
    || { echo "FAIL: ExecStartPre does not name taos-waydroid-check"; exit 1; }

# 4. Firewall, checked with the new file in place before the reload and
#    restored on failure, the same as pmos/nftables/install-firewall.sh.
[ -f "$NFT_DST" ] && cp -p "$NFT_DST" "$NFT_DST.bak"
install -m 644 "$NFT_SRC" "$NFT_DST"
if ! nft -c 'flush ruleset; include "/etc/nftables.nft"'; then
    echo "FAIL: ruleset check failed with $NFT_DST in place, restoring the previous file"
    if [ -f "$NFT_DST.bak" ]; then mv "$NFT_DST.bak" "$NFT_DST"; else rm -f "$NFT_DST"; fi
    exit 1
fi
rm -f "$NFT_DST.bak"
CONTAINER_WAS_ACTIVE=0
systemctl is-active --quiet $UNIT && CONTAINER_WAS_ACTIVE=1
systemctl reload nftables 2>/dev/null || systemctl restart nftables
nft list chain inet filter forward | grep -q 'iifname "waydroid0" accept' \
    && echo "PASS: waydroid0 accepted in inet filter forward" \
    || { echo "FAIL: waydroid0 rule not in the live forward chain after reload"; exit 1; }
nft list chain inet filter input | grep -q 'iifname "waydroid0" accept' \
    && echo "PASS: waydroid0 accepted in inet filter input" \
    || { echo "FAIL: waydroid0 rule not in the live input chain after reload"; exit 1; }

# 5. Init. "Initialised" is the config AND both images AND the rootfs dir,
#    never the config alone: a download that died half-way leaves the config.
initialised() {
    [ -f "$WORK/waydroid.cfg" ] && [ -f "$WORK/images/system.img" ] \
        && [ -f "$WORK/images/vendor.img" ] && [ -d "$WORK/rootfs" ]
}
if initialised; then
    echo "PASS: already initialised ($(du -sh "$WORK/images" | cut -f1) of images)"
else
    if systemctl is-active --quiet waydroid-init.service; then
        echo "waydroid-init already running, waiting for it"
    else
        # A previous failed attempt leaves the transient unit loaded (RemainAfterExit)
        # so its Result was readable; clear it before starting again.
        systemctl reset-failed waydroid-init.service 2>/dev/null || true
        systemctl stop waydroid-init.service 2>/dev/null || true
        echo "starting waydroid init -s $SYSTEM_TYPE (unit waydroid-init, log: journalctl -u waydroid-init)"
        systemd-run --quiet --unit waydroid-init -p RemainAfterExit=yes \
            /usr/bin/waydroid init -s "$SYSTEM_TYPE"
    fi
    # Poll the unit, not this shell: if ssh drops, re-run the script and it
    # lands back here.
    while systemctl is-active --quiet waydroid-init.service \
          && [ "$(systemctl show -p ExecMainCode --value waydroid-init.service)" = 0 ]; do
        echo "  $(date -u +%H:%M:%SZ) $(journalctl -u waydroid-init -n1 -o cat --no-pager 2>/dev/null | cut -c1-100)"
        sleep 20
    done
    STATUS="$(systemctl show -p ExecMainStatus --value waydroid-init.service)"
    systemctl stop waydroid-init.service 2>/dev/null || true
    systemctl reset-failed waydroid-init.service 2>/dev/null || true
    [ "$STATUS" = 0 ] || { echo "FAIL: waydroid init exited $STATUS (journalctl -u waydroid-init)"; exit 1; }
    initialised || { echo "FAIL: waydroid init exited 0 but config/images/rootfs are not all present"; ls -la "$WORK" "$WORK/images" 2>&1; exit 1; }
    echo "PASS: waydroid initialised ($(du -sh "$WORK/images" | cut -f1) of images)"
fi
# The GPU facts init decided on. gbm/mesa means hardware rendering through the
# msm render node; default/swiftshader means software and a bug to chase.
echo "waydroid_base.prop:"
grep -E '^ro\.hardware\.(gralloc|egl|vulkan)=|^gralloc\.gbm\.device=' "$WORK/waydroid_base.prop" | sed 's/^/  /'

# 6. The container manager. Enable, then (re)start so the ExecStartPre check
#    runs against this install; a firewall reload above wiped its nft tables if
#    it was already running, so a restart is right in both cases.
#    `waydroid container start` only claims the id.waydro.Container bus name:
#    the LXC container itself is launched by the Start() bus call a SESSION
#    makes (container_manager.do_start), so "unit active" plus the check's PASS
#    lines in its journal is all this step can prove. RUNNING comes with the
#    first session, below.
systemctl enable --quiet $UNIT
if [ "$CONTAINER_WAS_ACTIVE" = 1 ]; then systemctl restart $UNIT; else systemctl start $UNIT; fi
systemctl is-active --quiet $UNIT && echo "PASS: $UNIT active" \
    || { echo "FAIL: $UNIT not active"; systemctl status --no-pager $UNIT | tail -20; exit 1; }
INV="$(systemctl show -p InvocationID --value $UNIT)"
journalctl _SYSTEMD_INVOCATION_ID="$INV" -o cat --no-pager 2>/dev/null | grep -q '^PASS: .* handset lxc lines present' \
    && echo "PASS: ExecStartPre check ran for this start (harden line applied)" \
    || { echo "FAIL: no harden PASS line in this start's journal"; journalctl _SYSTEMD_INVOCATION_ID="$INV" -o cat --no-pager | tail -20; exit 1; }
grep -qxF 'lxc.cgroup2.devices.deny = b *:* rwm' "$WORK/lxc/waydroid/config" \
    && echo "PASS: block devices denied in $WORK/lxc/waydroid/config" \
    || { echo "FAIL: the block-device deny line is not in the lxc config"; exit 1; }

echo
echo "NOT PROVEN by this script, and it must not claim to be:"
echo "  - that the Android container boots: that happens with the first session,"
echo "    which needs the kiosk session running:"
echo "      taos-waydroid-session show      # starts the session, sets multi_windows,"
echo "                                      # shows Android's own UI as a window"
echo "      waydroid status                 # Container: RUNNING"
echo "      taos-shot                        # then put the panel back off"
echo "  - that Android has network: from inside, waydroid shell -- ping -c1 1.1.1.1"
