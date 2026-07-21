#!/bin/bash
# Restore the Lomiri shell if the taOS kiosk fails to appear after boot.
#
# System-level on purpose: the thing it rescues is the user session, so it
# cannot live inside it. Acts on the phablet user's systemd instance.
GRACE="${1:-150}"
KEEP=/var/lib/taos-kiosk-keep
LOG=/var/log/taos-shell-guard.log
U=phablet
UID_N=32011
RUN="XDG_RUNTIME_DIR=/run/user/$UID_N DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_N/bus"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

[ -f "$KEEP" ] && { log "keep flag set; disarmed"; exit 0; }
sleep "$GRACE"

# Look for the actual browser process, not the launcher script: a wrapper that
# is merely alive tells us nothing about whether anything reached the screen.
if pgrep -f "webapp-container --fullscreen" >/dev/null 2>&1; then
    log "kiosk is up; leaving the session alone"
    exit 0
fi

log "kiosk absent after ${GRACE}s — restoring the Lomiri shell"
su - "$U" -c "$RUN systemctl --user unmask lomiri-full-greeter.service" 2>/dev/null
su - "$U" -c "$RUN systemctl --user disable taos-kiosk-mir.service" 2>/dev/null
su - "$U" -c "$RUN systemctl --user start lomiri-full-greeter.service" 2>/dev/null
log "restored; shell: $(su - "$U" -c "$RUN systemctl --user is-active lomiri-full-greeter.service" 2>/dev/null)"
