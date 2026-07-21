#!/bin/bash
# taOS shell: a QML app running as a nested Mir 1 server, replacing Lomiri.
#
# Why QML rather than webapp-container: as a nested Mir server
# (QT_QPA_PLATFORM=mir1server) webapp-container's Oxide engine reports
# "No suitable graphics backend found" and segfaults. A plain QML app with
# QtWebEngine in the same role DOES initialise and load the page — the failure
# was Oxide-specific, not a limit of nested Mir servers.
#
# Why not Mir 2 / cage / Electron: see docs/android-kiosk-scope.md — all
# tested, all fail on this Halium device (cage powers the phone off).
set -u

LOG_DIR="$HOME/.taos-logs"; mkdir -p "$LOG_DIR"
CONTROLLER_URL="${TAOS_URL:-http://localhost:6969}"
PORT="${CONTROLLER_URL##*:}"; PORT="${PORT%%/*}"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG_DIR/qml-shell.log"; }

# Wait for the controller (TCP, not HTTP: it answers 401 before login).
waited=0
while ! (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do
    [ "$waited" -ge 90 ] && { log "controller not up after ${waited}s"; break; }
    sleep 2; waited=$((waited + 2))
done
exec 3<&- 2>/dev/null || true
log "controller wait ${waited}s; starting shell"

export QT_QPA_PLATFORM=mir1server
export MIR_SERVER_HOST_SOCKET=/run/mir_socket
export MIR_SERVER_NAME=taos-shell
# Serve on our own socket path. The default (/run/user/<uid>/mir_socket) is
# Lomiri's and a stale one left behind makes the server die with
# "bind() failed with error: Address already in use".
export MIR_SERVER_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/taos_mir_socket"
rm -f "$MIR_SERVER_FILE"
export QTWEBENGINE_DISABLE_SANDBOX=1
# Match the panel-less full-screen surface to the device.
export GRID_UNIT_PX="${GRID_UNIT_PX:-21}"

exec qmlscene -platform mir1server "$HOME/bin/Shell.qml" >> "$LOG_DIR/qml-shell.log" 2>&1
