# usb-signaller, patched

Alpine's `community/usb-signaller` 0.4.2 APKBUILD plus one patch, rebuilt as `-r1` so it
upgrades the stock `-r0` on the handset.

## Why

usb-signaller 0.4.2 decides whether an NCM gadget is in developer or tethering mode from the
global `/proc/sys/net/ipv4/ip_forward`. On the handset Docker, incus and NetworkManager turn
forwarding on, so its own `usb-signaller-developer` gadget is reported as `Tethering`
(measured 2026-10-16, `journalctl -u usb-signaller`: "Mode: Tethering" with the developer
gadget bound). A later `set_mode(developer_mode)` then passes the same-mode guard, recreates
`usb-signaller-developer` (which deletes the live one), and removes the "old" gadget by the same
name, which is the new one. The port is left unbound.

`0001-udc-classify-own-ncm-gadgets-by-name.patch` classifies usb-signaller's own gadgets by
name, keeps the old detection for other gadgets (the initramfs binds `g1` at boot), and skips
disabling and removing the old gadget when it has the new gadget's name. It adds unit tests,
which run with `cargo test` on the build host (`check()` is off: the cross linker cannot
link the test binary). The same patch is meant for upstream
(codeberg.org/DylanVanAssche/usb-signaller); drop this directory once a release carries it.

`pmos/kiosk/bin/taos-usb-mode` keeps its own guard (it does not re-ask for a mode that is
already bound), so the handset is safe on either build.

## Build (on the pmbootstrap host)

```sh
A=$(pmbootstrap config aports)
mkdir -p "$A/temp/usb-signaller"
cp pmos/usb-signaller/APKBUILD pmos/usb-signaller/usb-signaller.initd \
   pmos/usb-signaller/0001-*.patch "$A/temp/usb-signaller/"
pmbootstrap checksum usb-signaller   # only after changing the patch; copy APKBUILD back
pmbootstrap build usb-signaller --arch aarch64 --force
```

Run one pmbootstrap command at a time. The packages land in
`$(pmbootstrap config work)/packages/edge/aarch64/`.

## Install (on the handset, as root)

```sh
apk add --allow-untrusted ./usb-signaller-0.4.2-r1.apk ./usb-signaller-systemd-0.4.2-r1.apk
systemctl restart usb-signaller
```

This is not a kernel package, so installing it on the device is safe.
