# Kernel patches carried for spacewar

The kernel is `linux-postmarketos-qcom-sc7280` in pmaports, built from the
**fork** `github.com/sc7280-mainline/linux` at tag `v7.2.2-sc7280`.

## 0002-s5kjn1-add-19p2mhz-mclk.patch — FIXES BOTH CAMERAS

Upstream patch 1/2 of "Add Ultra Wide Camera Support for Nothing Phone (1)" by
Danila Tikhonov, Tested-by Vasiliy Doylov, posted 2026-08-01. Still UNDER REVIEW
upstream and **not in the fork** — verified against both `v7.2.2-sc7280` and the
fork's default branch `sc7280-7.2.y`, where the driver still reads:

```c
#define S5KJN1_MCLK_FREQ_24MHZ		(24 * HZ_PER_MHZ)
...
if (freq != S5KJN1_MCLK_FREQ_24MHZ)
	return dev_err_probe(..., -EINVAL,
			     "MCLK clock frequency %lu is not supported\n", freq);
```

### WHY THIS IS ONE PATCH FOR TWO CAMERAS

Measured on the handset (see `CAMERA-taosmobile-dev.md`):

- The DT declares BOTH sensors — `sony,imx471` (front, `camera@1a`) and
  `samsung,s5kjn1` (rear, `camera@2d`) — and every `cam_cc_mclk*` runs at
  **19200000**.
- The front driver BINDS. The rear driver REJECTS 19.2MHz and fails probe.
- The front sensor sits in `/dev/media0`'s graph with **0 links**, while every
  other entity is linked, and CAMSS logs no error.

CAMSS creates its sensor links in the async-notifier COMPLETE callback, which
only runs once EVERY DT-declared subdev has bound. The rear sensor never binds,
so the notifier never completes, so NO sensor links are made — and libcamera,
walking back from a video node, finds no sensor at all. **The rear camera's
clock check is what keeps the front camera dark.**

So fixing the rear probe is expected to bring up both. That is why this is one
flash and not two.

⚠ **The link is the first gate, not the last.** libcamera still has to configure
formats and the CSID/VFE path. Do not read "both sensors linked" as "camera
works".

### WHY NOT JUST ASK THE DT FOR 24MHz

It would be a one-line change and it is the wrong one. The sensor's PLL setup
depends on its input clock: this patch adds `EXTCLK_19P2MHZ_INTEGER/FRACTION`
and `VT_PRE_PLL_DIV/VT_PLL_MUL` values for 19.2MHz. Re-pointing the DT at 24MHz
would ask the board's clock tree for a rate the hardware may not produce
exactly, and a mismatch would fail probe again — costing a second flash, which
is the thing being avoided.

### APPLYING IT

In pmaports, `device/community/linux-postmarketos-qcom-sc7280/`:
1. copy this file in beside the existing `0001-*.patch`
2. add it to `source=`
3. `pmbootstrap checksum linux-postmarketos-qcom-sc7280`
4. `pmbootstrap build --force linux-postmarketos-qcom-sc7280`
5. flash the kernel from omarchy via `flash_kernel` — **NOT** an on-device
   `apk add`, which boots but hangs after `pmos_continue_boot`
   (see `FLASHING-taosmobile-dev.md`), and install matching modules as root
   (see the `flash_kernel` modules lesson).


## 0003-s5kjn1-link-freq-700mhz.patch — THE SECOND HALF, found by testing the first

With 0002 applied the MCLK error is GONE and the driver fails one step later:

```
s5kjn1 18-002d: no matching link frequencies found
s5kjn1 18-002d: error -ENOENT: failed to check HW configuration
```

The fork's DTS declares `link-frequencies = /bits/ 64 <600000000>` for the
s5kjn1 endpoint (line ~937), and the driver supports only
`S5KJN1_LINK_FREQ_700MHZ`. Upstream patch 2/2 uses `700000000`, so that is what
this sets. `data-lanes = <1 2 3 4>` already matches upstream and is untouched.

⚠ **ONLY the s5kjn1 endpoint changes.** The imx471 endpoint also declares
600MHz and its driver ACCEPTS it — that camera already binds, and "fix both
endpoints" would break a working one.

### HOW 0002 WAS PROVEN WITHOUT A FLASH

`s5kjn1` is a loadable MODULE and 0002 touches only that one file, so the
rebuilt `s5kjn1.ko.zst` was installed on the live handset and the driver
reloaded — no flash. vermagic matched (`7.2.2 SMP preempt mod_unload aarch64`,
same pkgrel), and `v4l2-async` waits indefinitely rather than timing out, so a
late-registering subdev is still eligible. The original module is backed up
on-device at `s5kjn1.ko.zst.orig`.

That is why the flash is now low-risk: the driver half is already known to work
on this hardware, and only the DTB value is unproven. **The DTB cannot be
hot-loaded, so the flash is still required for 0003.**

## ⚠ A MEASUREMENT TRAP I FELL INTO ANYWAY

Reading `link-frequencies` with `od -An -tu8 --endian=big` printed NOTHING, and
I read that as "the property does not exist" and wrote it up as such. **busybox
`od` has no `--endian`** — the flag makes od fail and emit nothing, so an absent
property and an unreadable one look identical.

This exact trap is already documented in
`pmos/kiosk/install-kiosk-continuity.sh`, in a comment about the PNG header
check, and I hit it regardless. Read DT cells with python3's `struct.unpack`
on this device, never `od --endian`.
