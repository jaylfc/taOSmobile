# linux-android-nothing-spacewar — Droidian kernel packaging

Debian packaging that turns the Nothing Phone (1) Halium kernel into Droidian
kernel packages (`linux-image-*`, `linux-bootimage-*`, `linux-headers-*`).

## Source

- Kernel: `Nonta72/android_kernel_nothing_sm7325`, branch `halium-11.0-rebase`
  (Linux 5.4.289) — the tree behind the working Ubuntu Touch port.
- Parameters: taken from that port's `deviceinfo` (`Nonta72/nothing-spacewar`,
  branch `halium-11.0`), i.e. a configuration already proven to boot.

## Notes

- **Boot image header v3.** v3 drops the per-image address fields and needs a
  separate `vendor_boot`, hence `KERNEL_BOOTIMAGE_GENERATE_VENDOR_BOOT = 1`.
- **Generated fragment.** `vendor/lahaina_ALLYES_GKI.config` is not in the
  kernel tree; the device's `build.sh` generates it. `debian/rules` reproduces
  that step before configure, or the fragment stack is incomplete.
- **Halium 11** → the adaptation package must depend on the matching
  `adaptation-hybris-api30-phone`. A mismatch here breaks the whole
  graphics/HAL stack even with a good kernel.
- `common_fragments` must be added as a submodule at `droidian/common_fragments`,
  pinned to the branch matching the kernel base version.

## Build

```
docker run --rm -v $PWD:/buildd/sources -v $PWD/../out:/buildd \
    -it quay.io/droidian/build-essential:current-amd64 bash
# inside:
apt-get install linux-packaging-snippets
cd /buildd/sources
rm -f debian/control && debian/rules debian/control
RELENG_HOST_ARCH="arm64" releng-build-package
```
