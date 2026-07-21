#!/bin/bash
# Boot-time safety net for the exclusive taOS session.
#
# If the kiosk does not come up within the grace period, put Ubuntu Touch back
# AND revert the boot configuration, so the next boot is a normal phone rather
# than a repeat of the failure. Runs as a system service so it is independent
# of any user session, SSH connection, or the graphical stack itself.
#
# Touch /var/lib/taos-kiosk-keep once the session is proven to retire the net.

GRACE="${1:-120}"
KEEP=/var/lib/taos-kiosk-keep
LOG=/var/log/taos-session-guard.log

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

[ -f "$KEEP" ] && { log "keep flag set; guard disarmed"; exit 0; }

sleep "$GRACE"

if pgrep -f "electron /home/phablet/electron/app" >/dev/null 2>&1; then
    log "kiosk is running; nothing to do"
    exit 0
fi

log "kiosk NOT running after ${GRACE}s — reverting to Ubuntu Touch"
systemctl disable taos-kiosk-cage.service 2>/dev/null
systemctl stop taos-kiosk-cage.service 2>/dev/null
systemctl unmask lightdm.service 2>/dev/null
systemctl enable lightdm.service 2>/dev/null
systemctl start lightdm.service 2>/dev/null
log "reverted; shell: $(systemctl is-active lightdm.service)"
