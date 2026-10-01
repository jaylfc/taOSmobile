obj-m := gh_probe.o

KDIR ?= $(HOME)/gunyah/linux-7.2.2-sc7280
# Alpine keeps the unsuffixed llvm-nm/objcopy/readelf only here.
export PATH := /usr/lib/llvm23/bin:$(PATH)
# modules_prepare writes no Module.symvers, so modpost cannot see vmlinux's exports.
# The kernel has no MODVERSIONS; the loader resolves the symbols, and insmod says if one is missing.
export KBUILD_MODPOST_WARN := 1

all:
	$(MAKE) LLVM=1 ARCH=arm64 -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) LLVM=1 ARCH=arm64 -C $(KDIR) M=$(CURDIR) clean
