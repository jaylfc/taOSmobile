#!/bin/sh
# Install the taOS kiosk session on the handset: the generic session from taOS,
# then the handset layer on top of it.
#
# Run as root ON the device. Idempotent.
#
# The generic session (sway config, launcher, session helpers, chromium policy,
# taos-kiosk.service) moved to taOS under session/. This script fetches it at
# the commit named in pmos/TAOS_SESSION_PIN and runs its install-session.sh,
# then installs what makes it a phone: the handset helpers, the sway drop-in
# with the power and volume keys, the Plasma Mobile fallback for the unit, the
# boot wordmark background and the device-class marker.
#
# TAOS_SESSION_SRC=<taOS checkout>/session skips the download and installs
# from that directory instead (offline, or testing a taOS branch). The pin is
# then NOT checked: the caller chose the source.
#
# The background is still the splash's own final frame, so the handover from
# plymouth to the kiosk is invisible: sway paints it between plymouth quitting
# and chromium's first frame. This does NOT hold plymouth open past its quit,
# deliberately. Keeping plymouthd alive to cover the gap means it holds DRM
# master while sway wants it, and if the handover script ever failed the splash
# would cover a working kiosk with no way for the user to get past it. A
# background image has no such failure mode: worst case it is missing and the
# gap is black.
set -eu

HERE="$(dirname "$0")"
PIN_FILE="$HERE/../TAOS_SESSION_PIN"
DROPIN_SRC="$HERE/etc/sway.d/50-handset.conf"
WORDMARK_SRC="$HERE/boot-wordmark.png"   # pre-rendered full-panel; see tools/render-kiosk-background.py
WORDMARK_DEST=/usr/share/taos/boot-wordmark.png

[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
[ -r "$DROPIN_SRC" ] || { echo "FAIL: $DROPIN_SRC not readable"; exit 1; }
[ -r "$WORDMARK_SRC" ] || { echo "FAIL: $WORDMARK_SRC not readable"; exit 1; }

# 1. The generic session from taOS, at the pinned commit.
WORK=""
cleanup() { [ -z "$WORK" ] || rm -rf "$WORK"; }
trap cleanup EXIT
if [ -n "${TAOS_SESSION_SRC:-}" ]; then
    SESSION="$TAOS_SESSION_SRC"
    echo "using taOS session from $SESSION (TAOS_SESSION_SRC; pin not checked)"
else
    PIN="$(tr -d ' \n' < "$PIN_FILE")"
    printf '%s' "$PIN" | grep -qxE '[0-9a-f]{40}' || {
        echo "FAIL: $PIN_FILE must hold one full 40-hex taOS commit, got '$PIN'"; exit 1; }
    WORK="$(mktemp -d)"
    echo "fetching taOS session at $PIN..."
    URL="https://codeload.github.com/jaylfc/taOS/tar.gz/$PIN"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$URL" -o "$WORK/taos.tar.gz"
    else
        wget -qO "$WORK/taos.tar.gz" "$URL"
    fi || { echo "FAIL: could not download taOS at $PIN"; exit 1; }
    tar -xzf "$WORK/taos.tar.gz" -C "$WORK"
    # codeload names the top directory <repo>-<sha>. Checking that name is the
    # evidence the tarball is the pinned commit and not whatever a redirect served.
    TOP="$(ls -d "$WORK"/taOS-"$PIN" 2>/dev/null || true)"
    [ -n "$TOP" ] || { echo "FAIL: tarball has no taOS-$PIN directory - not the pinned commit"
        ls "$WORK"; exit 1; }
    SESSION="$TOP/session"
fi
[ -r "$SESSION/install-session.sh" ] || { echo "FAIL: no install-session.sh in $SESSION"; exit 1; }
sh "$SESSION/install-session.sh"
echo "PASS: generic taOS session installed"

# 2. The background, at a taOS-owned path. This is a FULL-PANEL image with the
#    wordmark already scaled and centred the way taos.script draws it, NOT the
#    raw wordmark: swaybg's `center` does not scale, so the raw 1479px-wide
#    wordmark on a 1080px panel came out enlarged and cropped (seen on the
#    glass). Regenerate with tools/render-kiosk-background.py, which reads the
#    scale factor out of taos.script so the two cannot drift.
install -d -m 755 /usr/share/taos
install -m 644 "$WORDMARK_SRC" "$WORDMARK_DEST"
echo "installed $WORDMARK_DEST"

# 3. The handset helpers the drop-in and the session-mode API exec by absolute
#    path. Anything the drop-in names has to be installed by the same script
#    that installs the drop-in: a bindsym's exec output goes nowhere, so a
#    missing helper is a power key that silently does nothing.
for helper in taos-kiosk-power taos-kiosk-screen \
              taos-kiosk-dt2w taos-kiosk-dt2w-run taos-kiosk-power-hold \
              taos-kiosk-volume taos-sensord taos-kiosk-charger taos-kiosk-charge-play taos-shot \
              taos-power-apply taos-session-mode taos-usb-mode; do
    install -m 755 "$HERE/bin/$helper" "/usr/local/bin/$helper"
    echo "installed /usr/local/bin/$helper"
done

# 4. The sway drop-in. The generic config includes /etc/taos/sway.d/*.conf.
install -d -m 755 /etc/taos/sway.d
install -m 644 "$DROPIN_SRC" /etc/taos/sway.d/50-handset.conf
echo "installed /etc/taos/sway.d/50-handset.conf"
grep -qxF 'include /etc/taos/sway.d/*.conf' /etc/taos/sway-kiosk.conf || {
    echo "FAIL: /etc/taos/sway-kiosk.conf does not include /etc/taos/sway.d - the drop-in would be ignored"
    exit 1; }
echo "PASS: the generic config includes the drop-in directory"

# Every path the drop-in execs must exist and be executable. The loop above is
# not the check: the drop-in is what actually gets run.
MISSING=""
for path in $(grep -oE '/usr/local/bin/taos-[a-z0-9-]+' "$DROPIN_SRC" | sort -u); do
    [ -x "$path" ] || MISSING="$MISSING $path"
done
[ -z "$MISSING" ] || { echo "FAIL: sway drop-in execs missing binaries:$MISSING"; exit 1; }
# Control: prove the grep found paths, or the check passed by looking at nothing.
EXEC_COUNT="$(grep -oE '/usr/local/bin/taos-[a-z0-9-]+' "$DROPIN_SRC" | sort -u | wc -l)"
[ "$EXEC_COUNT" -ge 5 ] || {
    echo "FAIL: only $EXEC_COUNT taos-* paths found in the drop-in - the check above measured nothing"
    exit 1; }
echo "PASS: all $EXEC_COUNT taos-* paths named by the drop-in are installed and executable"

# 5. Plasma Mobile as the kiosk's fallback, in a unit drop-in.
install -d -m 755 /etc/systemd/system/taos-kiosk.service.d
install -m 644 "$HERE/systemd/taos-kiosk.service.d/10-plasma-fallback.conf" \
    /etc/systemd/system/taos-kiosk.service.d/10-plasma-fallback.conf
systemctl daemon-reload
systemctl show -p OnFailure taos-kiosk.service | grep -q 'plasma-mobile.service' \
    && echo "PASS: taos-kiosk.service falls back to plasma-mobile.service" \
    || { echo "FAIL: OnFailure=plasma-mobile.service not in effect after daemon-reload"; exit 1; }
# The line above cannot tell the drop-in from an older unit that still carries
# OnFailure= itself (a handset installed before the move does). Only systemd's
# list of loaded drop-ins proves this file is in effect.
systemctl show -p DropInPaths taos-kiosk.service | grep -q '/taos-kiosk.service.d/10-plasma-fallback.conf' \
    && echo "PASS: 10-plasma-fallback.conf is loaded" \
    || { echo "FAIL: 10-plasma-fallback.conf is not among the unit's drop-ins"; exit 1; }

# 5b. Switching between the taOS kiosk and Plasma Mobile.
#
# The controller's session-mode API (tinyagentos/routes/system.py) switches
# with ONE `systemctl start` of taos-kiosk.service or plasma-mobile.service
# (the two Conflict). It runs as the unprivileged `taos`, so a polkit rule
# scoped to exactly those two units authorises it -- not sudo. taOS.desktop is
# the way back from Plasma Mobile: a launcher entry that runs
# taos-session-mode kiosk. These lived in taOS's packaging/postmarketos,
# which nothing installed; Jay ruled 2026-09-29 that taOSmobile owns the
# device layer, so they are installed from here.
install -d -m 755 /etc/polkit-1/rules.d
install -m 644 "$HERE/etc/50-taos-session.rules" /etc/polkit-1/rules.d/50-taos-session.rules
install -d -m 755 /usr/share/applications
install -m 644 "$HERE/etc/taOS.desktop" /usr/share/applications/taOS.desktop
grep -q 'Exec=/usr/local/bin/taos-session-mode kiosk' /usr/share/applications/taOS.desktop \
    && [ -x /usr/local/bin/taos-session-mode ] \
    && echo "PASS: session switch installed (polkit rule, launcher, helper)" \
    || { echo "FAIL: session switch incomplete"; exit 1; }

# 6. This is a phone. The controller reads the marker for its device class,
#    so being a handset does not depend on the Plasma fallback being installed.
printf 'mobile\n' > /etc/taos/device-class
[ "$(cat /etc/taos/device-class)" = mobile ] && echo "PASS: /etc/taos/device-class = mobile" \
    || { echo "FAIL: /etc/taos/device-class not written"; exit 1; }

# --- verification -----------------------------------------------------------
# Each check needs a control, or "absent" and "never looked" read the same.

BG_LINE="$(grep -E '^[[:space:]]*output[[:space:]]+\*[[:space:]]+bg' /etc/taos/sway-kiosk.conf || true)"
[ -n "$BG_LINE" ] || { echo "FAIL: no 'output * bg' line in the installed config"; exit 1; }
BG_PATH="$(printf '%s\n' "$BG_LINE" | awk '{print $4}')"
[ -r "$BG_PATH" ] || { echo "FAIL: config points at $BG_PATH, which is not readable"; exit 1; }
echo "PASS: background image exists at $BG_PATH ($(stat -c%s "$BG_PATH") bytes)"

# The whole point is that it matches the panel, so check that rather than mere
# existence. PNG stores width/height as big-endian u32 at byte 16.
#
# Read it as raw hex, NOT with `od --endian=big`: busybox od has no --endian,
# so that flag made od fail, left both values EMPTY, and the comparison then
# reported "background is x but the panel is 1080x2400". The check failed
# closed, which is the right direction, but it had measured nothing at all.
PNG_HDR=$(od -An -tx1 -j16 -N8 "$BG_PATH" 2>/dev/null | tr -d " \n")
[ ${#PNG_HDR} -eq 16 ] || { echo "FAIL: could not read PNG header - measured nothing"; exit 1; }
PNG_W=$(( 0x$(printf '%s' "$PNG_HDR" | cut -c1-8) ))
PNG_H=$(( 0x$(printf '%s' "$PNG_HDR" | cut -c9-16) ))
PANEL=$(cat /sys/class/drm/*/modes 2>/dev/null | head -1)
[ -n "$PANEL" ] || { echo "FAIL: could not read the panel mode - measured nothing"; exit 1; }
if [ "${PNG_W}x${PNG_H}" = "$PANEL" ]; then
    echo "PASS: background is ${PNG_W}x${PNG_H}, panel is $PANEL"
else
    echo "FAIL: background is ${PNG_W}x${PNG_H} but the panel is $PANEL"
    echo "      re-render: python3 tools/render-kiosk-background.py ${PANEL%x*} ${PANEL#*x}"
    exit 1
fi

echo
echo "NOT PROVEN by this script, and it must not claim to be:"
echo "  - that the session starts, the power and volume keys reach the page, and"
echo "    double-tap wakes the panel. That needs a REBOOT and the glass."
echo "      journalctl -b 0 | grep -v COMMAND= | grep -c \"failed to execute 'swaybg'\"   # expect 0"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -ci taos-kiosk                      # control: >0"
echo "  and that swaybg is actually running (by name, never pgrep -f, which"
echo "  matches its own command line):"
echo "      ps -eo comm | grep -x swaybg"
