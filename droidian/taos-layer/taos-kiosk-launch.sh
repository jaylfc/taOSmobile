#!/bin/bash
# Resolve the URL the kiosk should open, then become the kiosk.
#
# WHY THIS EXISTS
# ---------------
# taos-kiosk.service used to hardcode --app=http://localhost:6969/. That is
# right in exactly one of the three states this device can be in:
#
#   mode unset  (fresh device)  nothing listens on :6969 -> dead page, and no
#                               way to reach the first-run helper on :6970
#   mode=local                  correct
#   mode=remote                 nothing listens on :6969 -- remote mode is the
#                               mode DEFINED by there being no local controller
#
# So the URL has to come from ~/.config/taosmobile/shell.conf, which is what
# docs/first-run-controller-choice.md requirement 3 says and what the first-run
# helper already writes. systemd cannot do this in the unit: ExecStart= takes no
# command substitution. Hence a wrapper.
#
# ONE FILE, NOT TWO
# -----------------
# Resolution and launch live together on purpose. A separate "resolver" that the
# test exercised and a launcher that did its own thing would be free to drift,
# and the drift would only show up on a device with no keyboard. --print-url
# stops after resolution so the test drives the SAME code the unit runs.
#
# WHICH WAY TO FAIL
# -----------------
# Every fallback here points at the first-run helper, never at :6969. That is
# the whole safety argument: the helper is the only screen from which a user
# holding a phone with no keyboard can fix a bad config. Falling back to a
# controller that is not there is a dead end; falling back to the helper is a
# way out.
#
# THE WAY BACK IN
# ---------------
# Everything above is about a config that is missing or malformed. A config that
# is well-formed and STALE -- the controller moved, was renamed, or the user
# simply changed their mind -- resolves cleanly to a target that is not there,
# every boot, forever. On a device with no keyboard that is the one-way door
# docs/first-run-controller-choice.md requirement 1 forbids.
#
# So resolution starts by looking for a sentinel that taos-setup-escape.py
# writes when the volume-up + volume-down chord is held. If it is there, this
# start goes to the first-run helper whatever the config says, and the sentinel
# is DELETED in the same breath, so the start after that is normal again.
#
# It is consumed on every resolution, including --print-url and --preflight.
# That is deliberate rather than an oversight: one contract -- "the next
# resolution goes to setup" -- has no special cases to get wrong, and it means
# the test drives the same consumption the unit does. The cost is that a
# diagnostic run over SSH eats a pending escape, and someone with an SSH shell
# is already past needing it.
#
# And when the resolved target is loopback and nothing is listening, this script
# EXITS NON-ZERO rather than launching. A kiosk showing "site can't be reached"
# is a successful start, so Restart= and OnFailure= never fire and the screen is
# stuck. Failing instead lets OnFailure=taos-kiosk-recover.service hand the
# display back to Phosh, which has a keyboard, a browser and a way back in.
#
# See docs/first-run-controller-choice.md and the layer README.

set -uo pipefail

FIRSTRUN_PORT="${TAOS_FIRSTRUN_PORT:-6970}"
FIRSTRUN_URL="http://127.0.0.1:${FIRSTRUN_PORT}/"
LOCAL_URL="http://localhost:6969/"

# Seconds to wait for a loopback target to start listening. The controller is a
# Python app importing torch-sized dependencies; the installer already allows it
# 120s. This is shorter because by the time the kiosk starts, systemd has
# ordered us After= it and it has had its own head start.
WAIT_SECS="${TAOS_KIOSK_WAIT:-45}"

# systemd sets HOME for User= units, but a hand-run of this script may not have
# it. Deriving it is cheaper than a confusing empty path.
if [ -z "${HOME:-}" ]; then
    HOME="$(getent passwd "$(id -un)" | cut -d: -f6)"
    export HOME
fi
CONF="${TAOS_SHELL_CONF:-$HOME/.config/taosmobile/shell.conf}"

# Must name the same file taos-setup-escape.py writes -- the same expression in
# two languages. It lives under XDG_RUNTIME_DIR, which is tmpfs: an orphaned
# sentinel (written when the restart then failed) cannot survive a reboot and
# strand the device in setup. Wrong direction to fail in would be the other one.
SENTINEL="${TAOS_SETUP_SENTINEL:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/taos-setup-requested}"

log() { echo "taos-kiosk-launch: $*" >&2; }

# Mirrors _URL_RE in taos-firstrun.py. Kept strict on purpose, and strictness
# here is not only about typos: this string becomes a chromium argument, and
# --unsafely-treat-insecure-origin-as-secure takes a COMMA-SEPARATED list, so a
# URL containing a comma would silently add trust for an origin nobody chose.
# No comma, no space and no quote can pass this pattern.
URL_RE='^https?://[A-Za-z0-9._~-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/%?=&-]*)?$'

valid_url() { [[ "$1" =~ $URL_RE ]]; }

# Read one key out of shell.conf. Plain key=value, # comments, blank lines --
# the same grammar taos-firstrun.py writes and reads.
conf_get() {
    local key="$1" line k v
    [ -r "$CONF" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"      # ltrim
        [ -z "$line" ] && continue
        case "$line" in \#*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        k="${line%%=*}"; v="${line#*=}"
        k="${k//[[:space:]]/}"
        v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
        if [ "$k" = "$key" ]; then printf '%s\n' "$v"; return 0; fi
    done < "$CONF"
    return 1
}

# Returns 0 if a setup escape is pending, and consumes it. Unlinking BEFORE
# answering, not after, is the point: if this script then dies, the sentinel is
# already gone and the next start is a normal one. The other order turns a crash
# loop into a device permanently in setup.
take_sentinel() {
    [ -e "$SENTINEL" ] || return 1
    if rm -f "$SENTINEL" 2>/dev/null && [ ! -e "$SENTINEL" ]; then
        log "setup escape requested; consumed $SENTINEL"
        return 0
    fi
    # Present and undeletable -- a chown that did not happen, most likely. Still
    # honour it: the user held the buttons. It costs one more trip through the
    # setup screen on the next start, and /run being tmpfs a reboot clears it.
    log "setup escape requested but $SENTINEL could not be removed; honouring it once more"
    return 0
}

resolve_url() {
    local mode url
    # Ahead of the config on purpose. The whole point is to override a config
    # that resolves perfectly well to somewhere the user cannot get back from,
    # so a mode= that parses is not a reason to ignore two held buttons.
    if take_sentinel; then
        printf '%s\n' "$FIRSTRUN_URL"; return
    fi
    if [ ! -r "$CONF" ]; then
        log "no readable config at $CONF -- first run"
        printf '%s\n' "$FIRSTRUN_URL"; return
    fi
    mode="$(conf_get mode)" || mode=""
    url="$(conf_get url)" || url=""

    case "$mode" in
        local)
            # A local install always serves :6969. Honour an explicit url= if it
            # is sane (the helper writes one), but never let a bad line in this
            # branch send us to the helper: local mode HAS a controller, and the
            # first-run screen would be a worse answer than the right default.
            if [ -n "$url" ] && valid_url "$url"; then
                printf '%s\n' "$url"
            else
                [ -n "$url" ] && log "mode=local with unusable url, using $LOCAL_URL"
                printf '%s\n' "$LOCAL_URL"
            fi
            ;;
        remote)
            # Here a bad url is NOT recoverable by guessing: there is no default
            # remote controller. Send the user back to the screen that can fix it.
            if valid_url "$url"; then
                printf '%s\n' "$url"
            else
                log "mode=remote but url is missing or malformed -- first-run helper"
                printf '%s\n' "$FIRSTRUN_URL"
            fi
            ;;
        "")
            log "config present but no mode set -- first run"
            printf '%s\n' "$FIRSTRUN_URL"
            ;;
        *)
            log "unknown mode '$mode' -- first-run helper"
            printf '%s\n' "$FIRSTRUN_URL"
            ;;
    esac
}

# scheme://host[:port] -- what chromium's origin flag wants. Not the full URL:
# passing a path there makes the flag silently match nothing.
origin_of() {
    local u="$1" rest
    rest="${u#*://}"
    printf '%s://%s\n' "${u%%://*}" "${rest%%/*}"
}

is_loopback_origin() {
    case "$1" in
        *://localhost|*://localhost:*|*://127.*|*://\[::1\]|*://\[::1\]:*) return 0 ;;
        *) return 1 ;;
    esac
}

# Wait for a loopback target, because on this device "nothing is listening" is a
# startup race as often as a real absence. Remote targets are deliberately NOT
# gated: a phone in remote mode is a SURFACE, its network comes and goes, and
# handing the display back to Phosh on a blip would be a worse failure than the
# unreachable-controller state requirement 2 already puts in the UI.
#
# Each attempt is bounded by `timeout`, because a bare /dev/tcp connect to an
# address that BLACKHOLES rather than refusing blocks for the kernel's SYN
# retry budget -- minutes, not seconds. Without the bound WAIT_SECS is a lower
# bound rather than a budget, and a single attempt can outlast the whole thing.
# Loopback refuses instantly so this never bites the paths gated today, but a
# probe whose timeout does not time out is a trap for whoever gates the next
# target. Host and port are passed as arguments, never interpolated into the
# -c string.
port_ready() {
    local host="$1" port="$2" deadline=$(( SECONDS + WAIT_SECS ))
    while true; do
        timeout 2 bash -c 'exec 3<>/dev/tcp/"$0"/"$1"' "$host" "$port" 2>/dev/null && return 0
        [ "$SECONDS" -ge "$deadline" ] && return 1
        sleep 1
    done
}

# Split an origin into host and port for the readiness probe.
host_of() { local r="${1#*://}"; r="${r%%/*}"; r="${r%:*}"; printf '%s\n' "$r"; }
port_of() {
    local r="${1#*://}"; r="${r%%/*}"
    case "$r" in
        *:*) printf '%s\n' "${r##*:}" ;;
        *)   case "$1" in https://*) echo 443 ;; *) echo 80 ;; esac ;;
    esac
}

# Build the command line ONCE. --print-argv prints exactly this array and exec
# runs exactly this array, so a test cannot pass against a command line the unit
# would not actually run. That is the same reason resolution lives in this file
# rather than in a separate resolver: no second copy, nothing to drift.
build_argv() {
    # The origin flag has to track the resolved URL. It was hardcoded to :6969,
    # which meant a remote http:// controller was NOT granted a secure context,
    # while a localhost one -- which chromium already treats as trustworthy
    # without any flag -- was. Only http origins need it; naming an https origin
    # here would be noise, and naming a URL rather than an origin makes the flag
    # silently match nothing.
    local origin_flag=()
    case "$ORIGIN" in
        http://*) origin_flag=("--unsafely-treat-insecure-origin-as-secure=$ORIGIN") ;;
    esac
    ARGV=(
        /usr/bin/cage -- /usr/bin/chromium
        --kiosk
        --user-data-dir=/run/taos-kiosk/profile
        "--app=$URL"
        --ozone-platform=wayland
        --enable-features=UseOzonePlatform,TouchpadOverscrollHistoryNavigation
        --touch-events=enabled
        --no-first-run
        --disable-infobars
        --disable-session-crashed-bubble
        --password-store=basic
        "${origin_flag[@]}"
    )
}

URL="$(resolve_url)"
ORIGIN="$(origin_of "$URL")"

case "${1:-}" in
    --print-url) printf '%s\n' "$URL"; exit 0 ;;
    --print-origin) printf '%s\n' "$ORIGIN"; exit 0 ;;
    --print-argv) ;;   # print the exact command line, do not run it
    --preflight) ;;    # resolve + readiness, then exit; do not take the display
    "") ;;
    *) log "unknown argument: $1"; exit 64 ;;
esac

if [ "${1:-}" = "--print-argv" ]; then
    build_argv
    printf '%s\n' "${ARGV[@]}"
    exit 0
fi

if is_loopback_origin "$ORIGIN"; then
    if ! port_ready "$(host_of "$ORIGIN")" "$(port_of "$ORIGIN")"; then
        # Exit non-zero ON PURPOSE. See "WHICH WAY TO FAIL" above.
        log "nothing listening on $ORIGIN after ${WAIT_SECS}s; refusing to take the display"
        exit 3
    fi
fi

[ "${1:-}" = "--preflight" ] && { log "preflight ok: $URL"; exit 0; }

build_argv
log "opening $URL"

exec "${ARGV[@]}"
