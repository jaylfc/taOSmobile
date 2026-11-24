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

die() { echo "FAIL: $*" >&2; exit 1; }

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

# One line per fact the personal tailnet exposes: backend state, own IPs, table 52.
snapshot() {
    st="$(tailscale status --json 2>/dev/null | tr -d ' \n' || true)"
    state="$(printf '%s' "$st" | sed -n 's/.*"BackendState":"\([^"]*\)".*/\1/p' | head -n1)"
    ips="$(printf '%s' "$st" | sed 's/.*"Self"://' \
        | grep -o '"TailscaleIPs":\[[^]]*\]' | head -n1 || true)"
    printf 'backend=%s\nips=%s\n' "${state:-unavailable}" "${ips:-none}"
    printf 'table52=%s\n' "$(ip route show table 52 2>/dev/null | tr '\n' ';' || true)"
}

snapshot > "$TMP"
echo "personal tailnet before:"; cat "$TMP"

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

AFTER="$(mktemp)"
trap 'rm -f "$TMP" "$AFTER"' EXIT
snapshot > "$AFTER"
if ! cmp -s "$TMP" "$AFTER"; then
    echo "personal tailnet changed after install, restarting tailscaled:"
    cat "$AFTER"
    systemctl restart tailscaled || die "restart tailscaled failed"
    i=0
    while :; do
        snapshot > "$AFTER"
        cmp -s "$TMP" "$AFTER" && break
        i=$((i + 1))
        [ "$i" -le "$SETTLE" ] || { cat "$AFTER"; die "personal tailnet still differs after restart"; }
        sleep 1
    done
fi
echo "PASS: personal tailnet BackendState and IPs unchanged"
echo "PASS: personal route table 52 unchanged"
