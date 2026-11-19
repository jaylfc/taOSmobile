#!/bin/bash
# Sourced by the guest builders: the units every guest root gets, all reporting to taOS's root, which
# guest-init leaves mounted at /mnt/taos. dualboot_guest_units ROOT EXE NAME TAG PRODUCT
#   ROOT    the guest's root as mounted now (for OSTree, the deployment)
#   EXE     where the two helper scripts live as the guest sees it (/usr/local/bin; /etc/dualboot on
#           OSTree, whose /usr is read-only and /usr/local leads into /var)
#   NAME    the guest in the marker's description, TAG names the marker file ($TAG-booted)
#   PRODUCT the USB gadget's product string
dualboot_guest_units() {
local R=$1 EXE=$2 NAME=$3 TAG=$4 PRODUCT=$5
local BIN=$R$EXE
mkdir -p "$BIN" "$R/etc/systemd/system" "$R/etc/NetworkManager/conf.d"
# taOS's root stays mounted at /mnt/taos (the initramfs moves it there); the marker proves Arch reached multi-user
cat > "$R/etc/systemd/system/dualboot-marker.service" <<EOF
[Unit]
Description=Record that the $NAME guest booted (read from taOS after switching back)
After=multi-user.target network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'mount -o remount,rw /mnt/taos 2>/dev/null; { date -u; uname -a; ip -br addr; } >> /mnt/taos/var/lib/dualboot/$TAG-booted'

[Install]
WantedBy=multi-user.target
EOF
# From sysinit on, a line per second on taOS's root: after a guest death it shows how long userspace lived
cat > "$BIN/dualboot-heartbeat" <<'EOF'
#!/bin/sh
f=/mnt/taos/var/lib/dualboot/heartbeat
echo "=== guest boot" >> "$f"
while :; do
  read -r up _ < /proc/uptime
  echo "$up $(systemctl is-system-running 2>/dev/null)" >> "$f"
  sync "$f"
  sleep 1
done
EOF
chmod 755 "$BIN/dualboot-heartbeat"
cat > "$R/etc/systemd/system/dualboot-heartbeat.service" <<EOF
[Unit]
Description=Write guest uptime to taOS's root each second
DefaultDependencies=no

[Service]
ExecStart=$EXE/dualboot-heartbeat

[Install]
WantedBy=sysinit.target
EOF
# Test mode, armed from taOS by touching $D/return-after (seconds): the guest reboots back to taOS
# on its own, so a run with no network in the guest still ends without a hand on the power key
cat > "$R/etc/systemd/system/dualboot-return.service" <<'EOF'
[Unit]
Description=Reboot back to taOS after the delay in taOS's return-after file
DefaultDependencies=no
ConditionPathExists=/mnt/taos/var/lib/dualboot/return-after

[Service]
ExecStart=/bin/sh -c 'sleep "$$(cat /mnt/taos/var/lib/dualboot/return-after)"; systemctl reboot || reboot -f'

[Install]
WantedBy=sysinit.target
EOF
# USB network like taOS's fallback (pmOS initramfs defaults: 18d1:d001, ncm, phone at 172.16.42.1), so the
# guest answers over the cable whether or not wifi comes up
cat > "$BIN/dualboot-usbnet" <<EOF
#!/bin/sh
set -eu
modprobe libcomposite
modprobe usb_f_ncm
mountpoint -q /sys/kernel/config || mount -t configfs configfs /sys/kernel/config
G=/sys/kernel/config/usb_gadget/g1
mkdir -p "\$G/strings/0x409" "\$G/configs/c.1/strings/0x409" "\$G/functions/ncm.usb0"
echo 0x18D1 > "\$G/idVendor"
echo 0xD001 > "\$G/idProduct"
echo $PRODUCT > "\$G/strings/0x409/product"
echo ncm > "\$G/configs/c.1/strings/0x409/configuration"
[ -e "\$G/configs/c.1/ncm.usb0" ] || ln -s "\$G/functions/ncm.usb0" "\$G/configs/c.1/"
ls /sys/class/udc > "\$G/UDC"
ip addr replace 172.16.42.1/24 dev usb0
ip link set usb0 up
EOF
chmod 755 "$BIN/dualboot-usbnet"
cat > "$R/etc/systemd/system/dualboot-usbnet.service" <<EOF
[Unit]
Description=USB network gadget at 172.16.42.1
After=systemd-modules-load.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$EXE/dualboot-usbnet

[Install]
WantedBy=multi-user.target
EOF
mkdir -p "$R/etc/NetworkManager/conf.d"
printf '[keyfile]\nunmanaged-devices=interface-name:usb0\n' > "$R/etc/NetworkManager/conf.d/50-usb0-unmanaged.conf"
}
