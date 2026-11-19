#!/bin/sh
# taos-touch-seq.sh: drive the glass from a script on stdin, as root on the handset.
#   lines: "shot NAME" -> $OUT/NAME.png (grim; OUT is a fresh root-owned dir, printed first) | "wait SECS" | "key KEY" (wtype, e.g. F5 reloads the kiosk) | any taos-touch command
# One persistent touch device for the whole run; wakes a dark panel first and ALWAYS
# powers it off at the end (Jay: no burn-in). Uses $TAOS_TOUCH, default the installed /usr/local/bin/taos-touch.
R=${R:-/run/user/$(id -u taos)}
ENV="XDG_RUNTIME_DIR=$R WAYLAND_DISPLAY=$(cd $R && ls | grep -m1 '^wayland-[0-9]*$') SWAYSOCK=$(ls $R/sway-ipc.*.sock|head -1)"
D=$(mktemp -d) || exit 1; F=$D/tt.fifo; P=
OUT=$(mktemp -d /tmp/taos-shots.XXXXXX) || exit 1; chmod 755 "$OUT"; echo "shots $OUT"
# never leave the panel lit (burn-in): EXIT runs on a normal end AND after INT/TERM
cleanup() {
  exec 3>&-
  [ -n "$P" ] && { kill $P 2>/dev/null; wait $P 2>/dev/null; }
  if su taos -s /bin/sh -c "$ENV /usr/local/bin/taos-kiosk-screen off" >/dev/null 2>&1; then echo panel off
  else echo "PANEL OFF FAILED: panel may be lit" >&2; rm -rf "$D"; exit 1; fi
  rm -rf "$D"
}
trap cleanup EXIT; trap 'exit 130' INT TERM HUP PIPE
if su taos -s /bin/sh -c "$ENV swaymsg -t get_outputs -r" | grep -q '"power": *false'; then
  su taos -s /bin/sh -c "$ENV /usr/local/bin/taos-kiosk-screen on" >/dev/null 2>&1; sleep 1.5; echo woke
fi
mkfifo -m 600 "$F" || exit 1
python3 "${TAOS_TOUCH:-/usr/local/bin/taos-touch}" - < "$F" & P=$!
exec 3>"$F"
sleep 1.2
while read -r c a1 rest; do
  case $c in
    shot) case $a1 in ''|*/*|.*) echo "bad shot name: $a1" >&2; continue ;; esac
      # grim writes to stdout as taos, root creates the file: nothing to chmod, no path taos controls
      if su taos -s /bin/sh -c "$ENV grim -" > "$OUT/$a1.png"; then echo shot $a1; else echo "shot $a1 FAILED" >&2; fi ;;
    wait) sleep $a1 ;;
    key) su taos -s /bin/sh -c "$ENV wtype -k $a1"; echo key $a1 ;;
    *) echo "$c $a1 $rest" >&3 ;;
  esac
done
exec 3>&-; wait $P; P=
