#!/bin/sh
# Install the taOS per-person mesh instance (slot 0) on the phone.
#
# Usage: install-mesh-instance.sh PERSON [--start]     (as root)
#
# Drives tinyagentos.taosnet.mesh_instances from taOS. Inert by default: the
# unit template and the slot are installed, nothing is enabled or started
# unless --start is given. The owner's personal kernel-tun tailscaled is only
# READ (status, route table 52); it is restarted only if the install changed
# what it reports, and the script fails if it still differs afterwards.
set -eu

TAOS_PYTHON="${TAOS_PYTHON:-/root/tinyagentos/.venv/bin/python}"
RUN_ROOT="${TAOS_MESH_RUN_ROOT:-/run/taos/mesh}"
SETTLE="${TAOS_MESH_SETTLE_SECS:-20}"
SOCK_WAIT="${TAOS_MESH_SOCKET_WAIT_SECS:-20}"
MOD=tinyagentos.taosnet.mesh_instances

PROVISIONED=0
VERIFYING=0
# A failure after provisioning began still checks (and if needed restores) the
# personal tailnet before exiting: a failed start must not leave it changed.
die() {
    echo "FAIL: $*" >&2
    if [ "$PROVISIONED" = 1 ] && [ "$VERIFYING" = 0 ]; then
        VERIFYING=1
        verify_personal || true
    fi
    exit 1
}

PERSON="${1:-}"
START=0
[ "${2:-}" = "--start" ] && START=1
[ "$#" -le 2 ] || die "usage: $0 PERSON [--start]"
[ "$#" -lt 2 ] || [ "$2" = "--start" ] || die "unknown argument: $2"
case "$PERSON" in
    "") die "usage: $0 PERSON [--start]" ;;
esac
# Anchored match through a case on the reverse of the shape: first char a-z,
# rest [a-z0-9-], at most 31 characters in total.
case "$PERSON" in
    [a-z]*) ;;
    *) die "bad person id: $PERSON" ;;
esac
if [ "${#PERSON}" -gt 31 ]; then die "bad person id (over 31 characters)"; fi
case "$PERSON" in
    *[!a-z0-9-]*) die "bad person id: $PERSON" ;;
esac

[ "$(id -u)" = 0 ] || die "must run as root"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# One line per fact the personal tailnet exposes: backend state, own IPs,
# table 52. Returns 1 when the state cannot be OBSERVED (daemon not answering,
# no BackendState, route query failed): "could not look" must never compare
# equal to "looked and nothing changed".
snapshot() {
    st="$(tailscale status --json 2>/dev/null | tr -d ' \n')" || return 1
    state="$(printf '%s' "$st" | sed -n 's/.*"BackendState":"\([^"]*\)".*/\1/p' | head -n1)"
    [ -n "$state" ] || return 1
    ips="$(printf '%s' "$st" | sed 's/.*"Self"://' \
        | grep -o '"TailscaleIPs":\[[^]]*\]' | head -n1 || true)"
    rt="$(ip route show table 52 2>/dev/null)" || return 1
    printf 'backend=%s\nips=%s\n' "$state" "${ips:-none}"
    printf 'table52=%s\n' "$(printf '%s' "$rt" | tr '\n' ';')"
}

OBSERVED=0
AFTER="$(mktemp)"
trap 'rm -f "$TMP" "$AFTER"' EXIT
if snapshot > "$TMP"; then
    OBSERVED=1
    echo "personal tailnet before:"; cat "$TMP"
else
    echo "NOT CHECKED: the personal tailnet could not be observed before install (tailscaled not answering on the default socket, or the route query failed); its state is not verified"
fi

verify_personal() {
    [ "$OBSERVED" = 1 ] || { echo "NOT CHECKED: personal tailnet (not observable before install)"; return 0; }
    if snapshot > "$AFTER" && cmp -s "$TMP" "$AFTER"; then
        echo "PASS: personal tailnet BackendState and IPs unchanged"
        echo "PASS: personal route table 52 unchanged"
        return 0
    fi
    echo "personal tailnet changed or unobservable after install, restarting tailscaled:"
    cat "$AFTER"
    systemctl restart tailscaled || { echo "FAIL: restart tailscaled failed" >&2; return 1; }
    i=0
    while :; do
        if snapshot > "$AFTER" && cmp -s "$TMP" "$AFTER"; then break; fi
        i=$((i + 1))
        if [ "$i" -gt "$SETTLE" ]; then
            cat "$AFTER"
            echo "FAIL: personal tailnet still differs after restart" >&2
            return 1
        fi
        sleep 1
    done
    echo "PASS: personal tailnet BackendState and IPs unchanged (after restart)"
    echo "PASS: personal route table 52 unchanged (after restart)"
}

PROVISIONED=1
"$TAOS_PYTHON" -m "$MOD" install-template || die "install-template failed"

OUT="$(su -s /bin/sh taos -c "\"$TAOS_PYTHON\" -m $MOD allocate $PERSON")" \
    || die "allocate $PERSON failed"
SLOT="$(printf '%s' "$OUT" | grep -o '"slot": *[0-9]*' | head -n1 | sed 's/[^0-9]//g')"
if [ "$SLOT" != 0 ]; then
    # Do not leave a stray allocation behind on a refused install.
    su -s /bin/sh taos -c "\"$TAOS_PYTHON\" -m $MOD release $PERSON" >/dev/null 2>&1 || true
    die "allocate reported slot '${SLOT:-none}', the phone's one person must be slot 0 (released)"
fi
echo "PASS: $PERSON holds slot 0"

if [ "$START" = 1 ]; then
    systemctl enable --now "taos-mesh@$PERSON" || die "enable --now taos-mesh@$PERSON failed"
    i=0
    while [ ! -e "$RUN_ROOT/$PERSON.sock" ]; do
        i=$((i + 1))
        [ "$i" -le "$SOCK_WAIT" ] || die "socket $RUN_ROOT/$PERSON.sock did not appear in ${SOCK_WAIT}s"
        sleep 1
    done
    echo "PASS: $RUN_ROOT/$PERSON.sock is up"
else
    echo "inert: not enabled or started (pass --start to run it)"
fi

verify_personal || exit 1
