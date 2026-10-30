# Dual boot: taOS (postmarketOS) + guest Linux OSes (Omarchy Mobile, Luma) on the handset

Owner: @taOSmobile-dev (device layer). Jay asked for it on 2026-10-30 and chose **true slot dual
boot**: one OS per A/B boot slot, sharing the userdata partition. He picked that direction over
the alternatives, so it is a decision. It is not a go for any write: every write to a boot slot
or to userdata still needs Jay's own go, at the device, at that step (software fastboot does not
work on this handset, so every flash needs his hands anyway).

Jay added Luma Mobile as a second guest on 2026-10-30 (section below).

Status: **stage 0 done 2026-10-30 (read-only).** Nothing has been written to the handset.

## What Omarchy Mobile is (read 2026-10-30)

- Arch Linux ARM + Hyprland on a mainline kernel (v7.3-rc6 branch `omamobile`). Upstream:
  github.com/dodola/omarchy-mobile, omamobile.org.
- The reference device is the Pixel 7 Pro. It supports Pixels and has a POCO F3 port proposed.
  **It has no Nothing, sm7325 or other Qualcomm phone port.** Mainline does carry
  `sm7325-nothing-spacewar.dts`, the same DTS our pmOS kernel builds from.
- `tools/install-phone` writes only the `*_b` boot images and `userdata`, and erases userdata.
  It never writes the bootloader, radio, persist or slot A. Running it on this handset would wipe
  taOS, so **we never run it here.**
- Its initramfs (`initramfs/init`, `boot_arch`) takes no `root=` argument. It looks up the
  partition with `PARTNAME=userdata` and mounts **the whole partition as bare ext4**. When the
  filesystem label is `omamobile-root`, it grows the filesystem to fill the partition
  (`grow_root`; `omamobile.noresize` skips this). **That initramfs therefore cannot share
  userdata with pmOS, so we do not use it.**
- Its CONTRIBUTING assumes a Pixel bootloader: DTB picked by `soc_id`, cmdline in
  `/chosen/bootargs`, an injected `/memory` and simplefb, and a board dtbo. It also notes the
  slot trap below.

## What the handset has today (stage 0, measured 2026-10-30)

- Slot b is active and running. boot_a and boot_b are both 96 MiB but **no longer identical**.
  Slot a holds `boot-r5-plygraphical.img` (kernel 7.2.2 build #5). Slot b holds build #9, which
  matches none of the saved images on the build host. A fallback to slot a boots an older build
  of the same kernel version. Both are dumped, with sha256 verified, under
  `~/dualboot-stage0-20261030/` on the build host.
- **Inner GPT in userdata, confirmed.** userdata (226.2 GiB) is loop-mapped. p1 `pmOS_boot` is
  480 MiB ext2. p2 `pmOS_root` is ext4 and runs to the end of the partition: 218.7 GiB with
  178.5 GiB free, so loop images fit easily.
- **pmOS keeps the root grown to the end.** On every boot, while the inner GPT has unallocated
  space, `resize_root_partition` (postmarketos-initramfs `init_functions_2nd.sh`) runs
  `parted resizepart 2 100%`. Once another inner partition takes that space, it finds none and
  does nothing.
- **Slot marking already works on taOS.** `qbootctl-systemd` 0.2.2 ships an enabled
  `qbootctl.service` (`qbootctl -m`). `qbootctl` reports slot b as Active 1, Successful 1,
  Bootable 1, and slot a as Active 0, Successful 0, Bootable 1. (Reading pmaports alone suggested
  that only OpenRC was wired up. The device proved that wrong.)
- **The slot trap (Omarchy CONTRIBUTING):** on Qualcomm A/B, flashing `boot_X` clears that slot's
  "successful" GPT attribute. A slot that is never marked successful burns its retry count, and
  abl then falls back to the other slot. Each OS must mark its own slot on every boot.

## Shape

```
 abl --(active slot a)--> boot_a: pmOS kernel (same build) + OUR small initramfs --> Arch root
 abl --(active slot b)--> boot_b: pmOS kernel + pmOS initramfs (unchanged)        --> taOS
                          userdata: inner GPT [pmOS_boot | pmOS_root | (Arch root, see Storage)]
 switch OS = set the other slot active (qbootctl from the running OS) + reboot
```

There is no boot menu: abl boots whichever slot is active. Each OS gets a "reboot into the other
OS" action that sets the other slot active and reboots. Fallback, with Jay's hands:
`fastboot set_active a|b`.

## Storage: where the Arch root lives

Jay picked option A on 2026-10-30.

| Option | What it costs |
|---|---|
| **A. Loop image file on pmOS_root** (recommended for stage 1) | No partition-table write at all. Our initramfs mounts pmOS_root and loop-mounts e.g. `/var/lib/dualboot/arch-root.img` as `/`. Arch then depends on pmOS's ext4 being clean, and a pmOS reflash of userdata deletes both OSes (true of every option). |
| B. Third inner partition (p3) | Needs an **offline shrink** of pmOS_root (e2fsck, resize2fs shrink, `parted resizepart 2`, `mkpart` p3) from outside the running pmOS, over the USB initramfs shell. Afterwards pmOS's resize finds no free space and leaves p3 alone. A failed shrink loses taOS's root, so it needs a full verified userdata backup first. |

Option A gets Omarchy running in days with no partition writes. It can move to B later, if the
space ever justifies it.

**Sizes (Jay 2026-10-30):** sparse image files, capped at 64G for Omarchy and 32G for Luma. A
cap is a ceiling: real use grows as the guest fills it. taOS keeps everything else and is never
shrunk. Raising or lowering a cap means resizing a file, not a partition table.

**Stock Nothing OS (Android): research only (Jay 2026-10-30).** Android mounts `/data` from the
partition named `userdata`, which taOS owns entirely. If that mount fails, Android offers a
factory reset that would format all of userdata, wiping taOS and every guest. Android also needs
its own kernel, vendor_boot, dtbo and vbmeta on its slot. A read-only study of whether `/data`
can be redirected without touching the outer GPT, and of how to close off the reset path, is in
progress. Nothing is written until it reports.

## Stages

**Stage 0 (done 2026-10-30, see above): read-only.** `lsblk`, `blkid` and `sgdisk -p` on the
loop-mapped userdata (inner GPT), `/proc/cmdline`, `qbootctl` slot state (current, successful,
unbootable, retry count) for both slots. Dump both boot slots over ssh **to the build host**
(never the phone's /tmp, which is tmpfs in RAM) with size and sha256, and check the two are
still identical. Fix this doc to match whatever differs.

**Stage 1: Arch Linux ARM root, booted from slot a by the existing pmOS kernel.**
- Build the root on the build host: ALARM aarch64 rootfs plus systemd, networkd, sshd and
  tailscale, with jay's key. Copy in the pmOS kernel's `/lib/modules/<uname -r>` and the
  firmware it loads, so the modules match the kernel exactly (a mismatch here is what cost wifi
  and battery after flash_kernel).
- Our initramfs: find userdata by PARTNAME, map its inner GPT, mount pmOS_root, loop-mount the
  Arch image, `switch_root` to systemd. If any step fails, stay in a shell reachable over USB
  (the pmOS USB network), the same way Omarchy's init does.
- Pack it with mkbootimg from deviceinfo: header v2, `append_dtb`, dtb
  `qcom/sm7325-nothing-spacewar`, pagesize 4096. Same kernel, so our DT patches (700MHz) come
  along.
- Slot marking: each guest marks slot a successful on every boot (`qbootctl -m`, built for the
  guest if it is not packaged there). taOS already does this for slot b.
- Flash `boot_a` only, with Jay at the device. **This removes the duplicate fallback kernel.**
  Recovery is `fastboot flash boot_a <stage-0 dump>`: a soft brick, never a hard one.

**Stage 2: Hyprland plus the Omarchy packages and config on that root.** No kernel change.

**Luma Mobile as a second guest (Jay 2026-10-30).** Luma (simplyluma.com) is Fedora-based and in
alpha. Its "Generic (for porting)" image ships with no kernel, firmware or boot image. Its porting
brief asks for a mainline-based kernel and device tree, which our pmOS kernel already provides.
Slot a becomes a guest-OS booter: our initramfs reads a "next OS" flag on pmOS_root and
loop-mounts `arch-root.img` or `luma-root.img` to match. To be checked once the 1.1 GB image is
unpacked: its format, and whether Fedora's SELinux expectations hold on our kernel. Omarchy comes
first; Luma follows on the same booter.

**Switching from an app (Jay asked 2026-10-30).** An "Omarchy" tile in taOS confirms, then asks a
root device helper (taOSmobile, alongside the taos-kiosk-* scripts; the page never runs root
commands itself) to set slot a active and reboot. Omarchy gets a matching "taOS" launcher entry
that sets slot b. A switch is a reboot, not an in-place launch. A slot that fails to come up burns
its retries and abl falls back to the other one, so this lands only after both OSes mark their own
slot successful. Otherwise a healthy OS gets bounced back.

**Stage 3 (maybe): Omarchy's own mainline kernel on slot a.** Only if stage 2 shows a reason.
It would need a spacewar DTB and none of the Pixel-bootloader assumptions above; for now it is
out of scope.

## Non-negotiables

- Never write xbl, abl, the GPT or super. Never relock the bootloader.
- Never run Omarchy's `install-phone`, or anything that grows a filesystem to fill userdata.
- Before any userdata write: a full backup piped over ssh to the build host, with size and
  sha256 checked.
- Every flash happens with Jay at the device, at that step.
