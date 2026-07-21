#!/bin/bash
# Exclusive taOS session: taOS owns the display, Lomiri is not running.
#
# SAFETY MODEL — read before changing anything here.
#
# 1. The only thing this script does is stop and start a *user* systemd unit
#    (lomiri-full-greeter.service). No partitions, no bootloader, no rootfs.
#    Ubuntu Touch remains installed and reflashable at all times.
# 2. `start` arms a watchdog. Unless someone confirms the session is good
#    (`taos-exclusive.sh keep`), Lomiri is restored automatically after DWELL
#    seconds. So a black screen fixes itself even if SSH is lost.
# 3. A reboot always comes back to Lomiri unless `enable-boot` was run.
#
# Usage:
#   taos-exclusive.sh start [dwell_seconds]   take the display (auto-reverts)
#   taos-exclusive.sh keep                    disarm the watchdog, stay in taOS
#   taos-exclusive.sh stop                    return to Ubuntu Touch now
#   taos-exclusive.sh status

set -u

SHELL_UNIT="lomiri-full-greeter.service"
KEEP_FLAG="$HOME/.taos-exclusive-keep"
LOG_DIR="$HOME/.taos-logs"
CONTROLLER_URL="${TAOS_URL:-http://localhost:6969}"
DEFAULT_DWELL=90

mkdir -p "$LOG_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG_DIR/exclusive.log"; }

kiosk_pid() { pgrep -f "webapp-container .*taos-exclusive" | head -1; }

start_kiosk() {
    # Try platform plugins in order of preference:
    #   mir1server — the app becomes its own Mir server (Qt plugin ships on UT)
    #   eglfs      — Qt straight onto KMS/DRM; this device has real msm_drm
    #                KMS plus Mesa freedreno, so this is a genuine fallback
    for platform in mir1server eglfs; do
        log "trying platform: $platform"
        # setsid + nohup: the session must outlive the SSH connection that
        # started it, or the display dies the moment the operator disconnects.
        # No --webappUrlPatterns: it blocks the first navigation (see
        # taos-kiosk.sh).
        setsid nohup env \
            QT_QPA_PLATFORM="$platform" \
            MIR_SERVER_NAME=taos-exclusive \
            webapp-container \
                --fullscreen \
                --store-session-cookies \
                --app-id=taos-exclusive \
                --name="taOS" \
                "$CONTROLLER_URL/" \
            >> "$LOG_DIR/exclusive-$platform.log" 2>&1 < /dev/null &
        local pid=$!
        sleep 12
        if kill -0 "$pid" 2>/dev/null; then
            log "kiosk alive on $platform (pid $pid)"
            echo "$platform" > "$LOG_DIR/active-platform"
            return 0
        fi
        log "platform $platform failed; see $LOG_DIR/exclusive-$platform.log"
    done
    return 1
}

restore_lomiri() {
    log "restoring Ubuntu Touch shell"
    pkill -f "webapp-container .*taos-exclusive" 2>/dev/null
    systemctl --user start "$SHELL_UNIT"
    sleep 3
    log "shell: $(systemctl --user is-active "$SHELL_UNIT")"
}

case "${1:-status}" in
start)
    DWELL="${2:-$DEFAULT_DWELL}"
    rm -f "$KEEP_FLAG"
    log "stopping $SHELL_UNIT"
    systemctl --user stop "$SHELL_UNIT"
    sleep 2

    if start_kiosk; then
        log "exclusive session up; watchdog will revert in ${DWELL}s unless 'keep' is run"
    else
        log "kiosk failed to start on any platform — reverting immediately"
        restore_lomiri
        exit 1
    fi

    # Watchdog: detached so it survives this script and any SSH disconnect.
    setsid bash -c "
        sleep $DWELL
        if [ ! -f '$KEEP_FLAG' ]; then
            echo \"\$(date -u +%FT%TZ) watchdog: no confirmation, reverting\" >> '$LOG_DIR/exclusive.log'
            pkill -f 'webapp-container .*taos-exclusive' 2>/dev/null
            systemctl --user start '$SHELL_UNIT'
        fi
    " >/dev/null 2>&1 < /dev/null &
    ;;

keep)
    touch "$KEEP_FLAG"
    log "watchdog disarmed; staying in the taOS session"
    ;;

stop)
    rm -f "$KEEP_FLAG"
    restore_lomiri
    ;;

status)
    echo "shell ($SHELL_UNIT): $(systemctl --user is-active "$SHELL_UNIT")"
    echo "kiosk pid:            $(kiosk_pid || echo none)"
    echo "platform:             $(cat "$LOG_DIR/active-platform" 2>/dev/null || echo none)"
    echo "watchdog disarmed:    $([ -f "$KEEP_FLAG" ] && echo yes || echo no)"
    echo "controller:           $(systemctl --user is-active taos-controller.service)"
    ;;

*)
    echo "usage: $0 {start [dwell]|keep|stop|status}" >&2
    exit 2
    ;;
esac
