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

# Shorten the shell's stop timeout so switching sessions is quick. A user
# drop-in under ~/.config — no system file is modified, and deleting the file
# restores stock behaviour.
ensure_stop_timeout_dropin() {
    local dir="$HOME/.config/systemd/user/${SHELL_UNIT}.d"
    local file="$dir/taos-stop-timeout.conf"
    [ -f "$file" ] && return 0
    mkdir -p "$dir"
    printf '[Service]\nTimeoutStopSec=6s\n' > "$file"
    systemctl --user daemon-reload
    log "installed stop-timeout drop-in for $SHELL_UNIT"
}

start_kiosk() {
    # Ubuntu Touch's display stack is three layers:
    #
    #   lomiri-system-compositor   owns the VT and the Android HWC, serves
    #                              /run/mir_socket                (system)
    #     └── lomiri (shell)       a Mir *client* of the above
    #           └── apps           clients of the shell
    #
    # So "exclusive" does not mean writing a compositor: it means stopping the
    # shell and connecting straight to the system compositor. That keeps a real
    # compositor underneath, which is what lets maliit (the on-screen keyboard)
    # and gestures work at all.
    #
    # Platform order:
    #   ubuntumirclient — client of lomiri-system-compositor. Preferred: real
    #                     compositor, HWC-backed, keyboard can attach.
    #   eglfs           — Qt directly on KMS. Works (this device has msm_drm +
    #                     Mesa) but one process owns the framebuffer, so there
    #                     is NO on-screen keyboard. Last resort only.
    for platform in ubuntumirclient eglfs; do
        log "trying platform: $platform"
        # setsid + nohup: the session must outlive the SSH connection that
        # started it, or the display dies the moment the operator disconnects.
        # No --webappUrlPatterns: it blocks the first navigation (see
        # taos-kiosk.sh).
        # Point Mir clients at the *system* compositor socket, not the shell's
        # nested one (/run/user/<uid>/mir_socket) — that one dies with the
        # shell and refuses connections outside lomiri-app-launch.
        setsid nohup env \
            QT_QPA_PLATFORM="$platform" \
            MIR_SOCKET=/run/mir_socket \
            MIR_SERVER_HOST_SOCKET=/run/mir_socket \
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
    # Unmask first, or the start below is a silent no-op and the phone is left
    # with no shell at all.
    systemctl --user unmask "$SHELL_UNIT" 2>/dev/null
    systemctl --user start "$SHELL_UNIT"
    sleep 3
    log "shell: $(systemctl --user is-active "$SHELL_UNIT")"
}

case "${1:-status}" in
start)
    DWELL="${2:-$DEFAULT_DWELL}"
    rm -f "$KEEP_FLAG"
    # The greeter's TimeoutStopSec is 90s and it routinely sits in
    # stop-sigterm, so a blocking stop would black the screen for a minute and
    # a half. Shorten it with a user-level drop-in (lives in /home, reversible,
    # no system files touched).
    ensure_stop_timeout_dropin

    # Mask before stopping. A plain stop is not enough: the greeter is pulled
    # back in by ubuntu-touch-session.target (and Restart=on-failure turns any
    # direct kill into an instant respawn), so the shell reappears seconds
    # later and the top bar comes back with it. Masking is reversible and
    # user-level; `stop` alone is not.
    log "masking $SHELL_UNIT"
    systemctl --user mask "$SHELL_UNIT" 2>/dev/null

    # Stop through systemd, never by killing the process.
    log "stopping $SHELL_UNIT"
    systemctl --user stop "$SHELL_UNIT" 2>/dev/null

    # Clear leftover app clients. The system compositor stays up — it owns the
    # display and we are about to connect to it. maliit is left running: it is
    # a Mir client and can serve the kiosk too.
    for p in $(pgrep -f webapp-container) $(pgrep -f QtWebEngineProcess); do
        kill "$p" 2>/dev/null
    done

    # systemctl returns before the process is reaped (the unit sits in
    # stop-sigterm until TimeoutStopSec). Wait for the process to actually go,
    # rather than declaring failure while it is still shutting down.
    waited=0
    while pgrep -x lomiri >/dev/null 2>&1 && [ "$waited" -lt 25 ]; do
        sleep 2; waited=$((waited + 2))
    done

    if pgrep -x lomiri >/dev/null 2>&1; then
        log "FATAL: shell still running after ${waited}s — aborting rather than fighting systemd"
        exit 1
    fi
    log "shell down after ${waited}s"

    if ! pgrep -f lomiri-system-compositor >/dev/null 2>&1; then
        log "FATAL: system compositor is not running; aborting before we lose the display"
        restore_lomiri
        exit 1
    fi
    log "shell down, system compositor still up"

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
            # MUST unmask before start: the session masks the shell, and
            # 'start' on a masked unit is a silent no-op — that would leave
            # the phone with no shell AND no kiosk, i.e. a dead screen.
            systemctl --user unmask '$SHELL_UNIT' 2>/dev/null
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
