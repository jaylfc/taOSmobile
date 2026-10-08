#!/bin/sh
# taos-touch-seq.sh: drive the glass from a script on stdin, as root on the handset.
#   lines: "shot NAME" -> /tmp/NAME.png (grim) | "wait SECS" | "key KEY" (wtype, e.g. F5 reloads the kiosk) | any taos-touch command
# One persistent touch device for the whole run; wakes a dark panel first and ALWAYS
# powers it off at the end (Jay: no burn-in). Uses $TAOS_TOUCH, default the installed /usr/local/bin/taos-touch.
R=${R:-/run/user/$(id -u taos)}
ENV="XDG_RUNTIME_DIR=$R WAYLAND_DISPLAY=$(cd $R && ls | grep -m1 '^wayland-[0-9]*$') SWAYSOCK=$(ls $R/sway-ipc.*.sock|head -1)"
if su taos -s /bin/sh -c "$ENV swaymsg -t get_outputs -r" | grep -q '"power": *false'; then
  su taos -s /bin/sh -c "$ENV /usr/local/bin/taos-kiosk-screen on" >/dev/null 2>&1; sleep 1.5; echo woke
fi
rm -f /tmp/tt.fifo; mkfifo /tmp/tt.fifo
python3 "${TAOS_TOUCH:-/usr/local/bin/taos-touch}" - < /tmp/tt.fifo & P=$!
exec 3>/tmp/tt.fifo
sleep 1.2
while read -r c a1 rest; do
  case $c in
    shot) su taos -s /bin/sh -c "$ENV grim /tmp/$a1.png"; chmod 644 /tmp/$a1.png; echo shot $a1 ;;
    wait) sleep $a1 ;;
    key) su taos -s /bin/sh -c "$ENV wtype -k $a1"; echo key $a1 ;;
    *) echo "$c $a1 $rest" >&3 ;;
  esac
done
exec 3>&-; wait $P
# never leave the panel lit (burn-in): always power it off when the sequence ends
su taos -s /bin/sh -c "$ENV /usr/local/bin/taos-kiosk-screen off" >/dev/null 2>&1; echo panel off
