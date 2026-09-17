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
