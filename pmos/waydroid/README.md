# Waydroid on the handset: Android apps as windows in taOS

Jay (2026-10-30): quick switching between mobile OSes is a key taOSmobile feature,
and the first step is Android apps running as native windows next to Linux apps
in the taOS sway session. A full-Android boot slot is a separate plan
(the dual-boot plan in PR #23). This directory is the container path.

## What runs where

- The Android container is root's: `waydroid-container.service` from the Alpine
  `waydroid-systemd` subpackage, with the handset drop-in
  `systemd/waydroid-container.service.d/10-taos.conf` (ExecStartPre check and
  harden, a real ExecStop). Waydroid loop-mounts `system.img` and `vendor.img`
  on the host and starts an LXC container over them.
- The session is the kiosk user's (`taos`): `bin/taos-waydroid-session` starts
  `waydroid session start` with the kiosk session's `XDG_RUNTIME_DIR` and
  `WAYLAND_DISPLAY`, so Android surfaces land in the sway session as windows.
  The unit alone only registers the container manager on the system bus; the
  LXC container boots when the first session calls it, and the session helper
  sets `persist.waydroid.multi_windows` once Android answers (a property can
  only be set through a running session).
  `taos-waydroid-session show` brings up Android's own UI as one window;
  `taos-waydroid-session app <pkg>` launches one app.
- `bin/taos-waydroid-check` refuses to start the container on a kernel that
  cannot run it (binder nodes, an msm render node, cgroup2, PSI) and appends
  `lxc-taos.conf` to the generated LXC config before every start: Android
  never gets a block device, so the UFS partitions stay out of reach whatever
  runs inside. `waydroid init`/`upgrade` regenerate that config, which is why
  the lines are re-applied at start rather than edited in once.
- Audio: the session config bind-mounts the kiosk user's Pulse socket into the
  container as a non-optional entry, and lxc refuses to start without it
  (measured 2026-10-31). The installer adds `pipewire-pulse` and enables its
  user socket for `taos`, so Android audio reaches PipeWire over the Pulse
  protocol.
- `51_waydroid.nft` lets the `waydroid0` bridge through the firewall's
  policy-drop input and forward chains (same shape as `51_docker.nft`).

## Install

```sh
sudo sh pmos/waydroid/install-waydroid.sh          # VANILLA image (default)
WAYDROID_SYSTEM_TYPE=GAPPS sudo sh pmos/waydroid/install-waydroid.sh
```

Idempotent and resumable. The image download (~1 GB, to
`/var/lib/waydroid/cache_http`, on disk, not the tmpfs `/tmp`) runs in a
transient unit `waydroid-init`, so it survives a dropped ssh; re-run the script
and it waits for or resumes the init. The script prints the GPU facts init
chose (`ro.hardware.gralloc`, `ro.hardware.egl`, `ro.hardware.vulkan`):
`gbm` / `mesa` / `freedreno` is hardware rendering through the Adreno 642L;
`default` / `swiftshader` is software and a bug.

Firewall ordering matters: `/etc/nftables.nft` begins with `flush ruleset`, and
`waydroid-net.sh` adds its own `inet lxc` and `ip lxc` tables at container
start, so a firewall reload while the container runs wipes them. After any
`systemctl reload nftables`, `systemctl restart waydroid-container`.

## Kernel facts (r9, Linux 7.2.2, measured on the handset 2026-10-31)

Passes pmOS's `category:waydroid` kconfig check in full. Static binder nodes
(`/dev/{binder,hwbinder,vndbinder}`, no binderfs by design), `renderD128`
bound to `msm_dpu`, cgroup2 at `/sys/fs/cgroup`, PSI on. Still missing
against omarchy's stricter Pixel check: `INET_DIAG_DESTROY` and the
`XT_MATCH_{CONNMARK,OWNER,QUOTA,BPF,PKTTYPE,U32,SOCKET}` /
`XT_TARGET_IDLETIMER` modules. Whether Android's netd needs them on this
image is measured under Android first; only what is shown missing goes into
the next kernel build.

## Tests

```sh
python3 -m pytest -q -p no:cacheprovider pmos/waydroid/test_taos_waydroid_check.py
```

Each test breaks one fact of a fake handset tree and proves the check fails on
that fact; the good tree passes and the harden line is appended exactly once.

## Not yet

Multi-window layout in the kiosk session (each app on its own sway workspace,
launcher entries in taOS), audio and camera into the container, and the
measurements under Android that decide the r10 netfilter modules.
