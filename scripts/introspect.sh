#!/usr/bin/env bash
# Device validation pass (implementation plan, task 1).
#
# Runs DBus introspection on the Ubuntu Touch phone over SSH and writes a log
# to docs/. Findings from this log get summarised into docs/device-notes.md,
# which is what tasks 4 and 6 are written against. Do not write oFono or
# telephony code before this has run on the real device.
#
# Usage: scripts/introspect.sh [ssh-host]     (default host: taosphone)

set -uo pipefail

HOST="${1:-taosphone}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/docs"
LOG="$OUT_DIR/introspect-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$OUT_DIR"

say() { printf '\n=== %s ===\n' "$1" | tee -a "$LOG"; }
probe() {
  # probe <label> <remote command>
  say "$1"
  ssh -o BatchMode=yes "$HOST" "$2" 2>&1 | tee -a "$LOG"
}

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true 2>/dev/null; then
  echo "Cannot SSH to '$HOST'." >&2
  echo "Check: key in the phone's ~/.ssh/authorized_keys, SSH enabled" >&2
  echo "(sudo android-gadget-service enable ssh), and a Host entry for" >&2
  echo "'$HOST' in ~/.ssh/config. Phone must be awake and on Wi-Fi." >&2
  exit 1
fi

echo "Logging to $LOG"
probe "device identity" "cat /etc/os-release; uname -a; getprop ro.product.model 2>/dev/null"
probe "glibc version (bridge must not exceed this)" "ldd --version | head -1"

probe "system bus services of interest" \
  "busctl list --no-pager | grep -iE 'ofono|upower|telephony|dispatcher' || echo 'NONE FOUND'"
probe "session bus services of interest" \
  "busctl --user list --no-pager | grep -iE 'telephony|history|dispatcher|lomiri|ubuntu' || echo 'NONE FOUND'"

probe "oFono modems (never hardcode the path — enumerate)" \
  "busctl call org.ofono / org.ofono.Manager GetModems"
probe "oFono modem introspection (expect org.ofono.MessageManager)" \
  "for m in \$(busctl call org.ofono / org.ofono.Manager GetModems 2>/dev/null | grep -oE '\"/[a-zA-Z0-9_/]+\"' | tr -d '\"'); do
     echo \"--- modem: \$m\"; busctl introspect org.ofono \"\$m\" --no-pager | grep -iE 'ofono|interface'; done"

probe "UPower display device (battery percent + charge state)" \
  "busctl introspect org.freedesktop.UPower /org/freedesktop/UPower/devices/DisplayDevice --no-pager | grep -iE 'percentage|state|property'"

probe "url-dispatcher availability (dial fallback)" \
  "which lomiri-url-dispatcher url-dispatcher 2>/dev/null || echo 'NEITHER FOUND'"

cat <<EOF | tee -a "$LOG"

=== manual steps (not automated — need a real SIM and a second phone) ===

1. Inbound SMS signal shape. Run this, then text the phone from another phone:
     ssh $HOST "dbus-monitor --system \"interface='org.ofono.MessageManager'\""
   Record the exact signal name and argument shape (text + info dict keys such
   as Sender and SentTime) — task 4 parses this.

2. Outbound SMS. With <modem-path> from above and a number you control:
     ssh $HOST "busctl call org.ofono <modem-path> org.ofono.MessageManager SendMessage ss '<number>' 'taOS bridge test'"

3. Dial probe. Try the telephony-service interface found on the session bus.
   Timebox to about an hour; if it is unusable, verify the fallback:
     ssh $HOST "lomiri-url-dispatcher 'tel:<number>'"
   and record which path we are taking.

4. Battery signals. Watch while unplugging and replugging the charger:
     ssh $HOST "dbus-monitor --system \"interface='org.freedesktop.DBus.Properties'\" | grep -i -A5 upower"

Summarise all findings into docs/device-notes.md. Do not commit real phone
numbers, IP addresses, or tokens.
EOF

echo
echo "Done. Log: $LOG"
