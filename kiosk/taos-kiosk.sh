#!/bin/bash
# Launch the taOS PWA as a fullscreen kiosk surface.
#
# Runs webapp-container (QtWebEngine, the same engine Morph uses) with no
# browser chrome, pointed at the controller running natively on this device.
#
# Nothing here modifies the system: it is an ordinary user-session app. Closing
# it returns to Lomiri, and it touches nothing outside /home.

set -u

CONTROLLER_URL="${TAOS_URL:-http://localhost:6969}"
BOOT_TIMEOUT="${TAOS_BOOT_TIMEOUT:-90}"
LOG_DIR="$HOME/.taos-logs"
mkdir -p "$LOG_DIR"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG_DIR/kiosk.log"; }

# The controller is a local service that may still be starting when the
# session comes up. Wait for it rather than showing the user a connection
# error — but never wait forever, or a broken controller means a dead screen.
#
# Probe the TCP port, not an HTTP fetch: before onboarding the controller
# answers 401, which every HTTP client reports as failure even though the
# server is up and ready to show the onboarding screen.
PORT="${CONTROLLER_URL##*:}"
PORT="${PORT%%/*}"
waited=0
while ! (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do
    [ "$waited" -ge "$BOOT_TIMEOUT" ] && break
    sleep 2
    waited=$((waited + 2))
done
exec 3<&- 2>/dev/null || true

if [ "$waited" -ge "$BOOT_TIMEOUT" ]; then
    log "controller did not answer within ${BOOT_TIMEOUT}s; launching anyway"
else
    log "controller up after ${waited}s"
fi

# --fullscreen          no chrome, fills the display
# --webappUrlPatterns   confine navigation to the local controller, so a stray
#                       link cannot turn the kiosk into a general browser
# --store-session-cookies  keep the taOS login across restarts
exec webapp-container \
    --fullscreen \
    --store-session-cookies \
    --local-content-can-access-remote-urls \
    --app-id=taosmobile \
    --name="taOS" \
    --webappUrlPatterns="http://localhost:6969/*,http://127.0.0.1:6969/*" \
    "$CONTROLLER_URL/" \
    >> "$LOG_DIR/kiosk.log" 2>&1
