#!/bin/bash
# taOS kiosk as a Mir client of lomiri-system-compositor.
#
# This is the only exclusive path that does not crash the device. The display
# is owned by lomiri-system-compositor, which drives the Android hwcomposer
# HAL via libhybris (mir1/server-platform/graphics-android2.so). Anything that
# tries to take DRM from it — cage/wlroots, Qt eglfs — powers the phone off.
# So: leave the compositor alone, replace only the shell on top of it.
set -u

LOG_DIR="$HOME/.taos-logs"; mkdir -p "$LOG_DIR"
CONTROLLER_URL="${TAOS_URL:-http://localhost:6969}"
PORT="${CONTROLLER_URL##*:}"; PORT="${PORT%%/*}"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG_DIR/kiosk-mir.log"; }

# Inherited from ubuntu-touch-session; set explicitly so the unit works even
# if the session environment is not fully imported yet.
export QT_QPA_PLATFORM=ubuntumirclient
# With the Lomiri shell masked, its nested socket (/run/user/<uid>/mir_socket)
# never exists — connect to the system compositor directly instead.
if [ -S "/run/user/$(id -u)/mir_socket" ]; then
    export MIR_SOCKET="/run/user/$(id -u)/mir_socket"
else
    export MIR_SOCKET=/run/mir_socket
fi
export MIR_SERVER_HOST_SOCKET="$MIR_SOCKET"
log "using mir socket: $MIR_SOCKET"

# The controller is a local service that may still be starting. Probe the TCP
# port: before onboarding it answers 401, which HTTP clients call a failure.
waited=0
while ! (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do
    [ "$waited" -ge 90 ] && { log "controller not up after ${waited}s; starting anyway"; break; }
    sleep 2; waited=$((waited + 2))
done
exec 3<&- 2>/dev/null || true
log "controller wait: ${waited}s"

exec webapp-container \
    --fullscreen \
    --store-session-cookies \
    --local-content-can-access-remote-urls \
    --app-id=taos-kiosk \
    --name="taOS" \
    "$CONTROLLER_URL/" \
    >> "$LOG_DIR/kiosk-mir.log" 2>&1
