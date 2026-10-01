# Gunyah probe (Nothing Phone 1, spacewar)

Jay (2026-10-01) asked whether taOS can run VMs on the handset through the firmware hypervisor,
since KVM reports `HYP mode not available` (Linux runs at EL1). The strings in `hyp_a` say that
hypervisor is Qualcomm Gunyah. `gh_probe.c` asks it directly. It is read-only and never stays
loaded: init always returns `-EAGAIN`, so `insmod` printing "Resource temporarily unavailable"
is the normal result.

## Result (kernel 7.2.2 #9, 2026-10-01)

| Call | Conduit | Answer | File |
|---|---|---|---|
| vendor-hyp UID `0x8600FF01` | HVC | **Oops: Undefined instruction** at the `hvc #0` | `PROBE-20261001-hvc-undef.txt` |
| vendor-hyp UID `0x8600FF01` | SMC | `19bd54bd 0b37571b 946f609b 54539de6` = `QC_HYP_UID` (Berman Gunyah v5 05/13) | `PROBE-20261001-smc-uid.txt` |
| HYP_IDENTIFY `0xC6008000` | SMC | `-1` NOT_SUPPORTED | `PROBE-20261001-smc-identify.txt` |

The firmware is Qualcomm Gunyah, but its hypercall API is closed to the Linux VM. The UNDEF
applies to every HVC, whatever the immediate, so Gunyah's native `hvc #0x6000` is closed too.
Over SMC the hypervisor answers only the standard UID discovery call. Without hypercalls,
Linux cannot create Gunyah VMs: no VM manager, and no KVM-on-Gunyah (the Manaouil RFC).

Opening that API needs a different hypervisor configuration, which lives in signed firmware
(`hyp`). This project never writes `hyp`, `xbl` or `abl`.

The HVC load oopses the kernel (taint D) and leaves a half-loaded module behind, so reboot
afterwards. Only the SMC calls are safe to repeat.

## Build (on the handset, as jay)

```sh
apk add clang23 lld23 llvm23 make bison flex bc perl musl-dev linux-headers openssl-dev elfutils-dev pahole
# tree: the APKBUILD's linux-v7.2.2-sc7280.tar.gz (check its sha512), unpacked to ~/gunyah/
cd ~/gunyah/linux-7.2.2-sc7280
export PATH=/usr/lib/llvm23/bin:$PATH   # without it olddefconfig silently drops LTO
zcat /proc/config.gz > .config && make LLVM=1 ARCH=arm64 olddefconfig
# diff against /proc/config.gz: only *_VERSION and CC_CAN_LINK may differ (pahole missing -> BTF
# dropped -> "struct module size" rejection)
make LLVM=1 ARCH=arm64 -j8 modules_prepare
cd <this dir> && make
sudo insmod gh_probe.ko smc=1 [identify=1]; dmesg | grep gh_probe
```
