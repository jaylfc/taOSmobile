# Droidian kernel packaging for the Nothing Phone (1) — codename spacewar.
#
# Values come from the Ubuntu Touch port's deviceinfo
# (Nonta72/nothing-spacewar, branch halium-11.0), which is the configuration
# proven to boot on this device.

VARIANT = android
KERNEL_BASE_VERSION = 5.4.289

# From deviceinfo_kernel_cmdline, plus the two entries Droidian requires:
# console=tty0 (already present upstream) and droidian.lvm.prefer.
# No systempart= entry to strip.
KERNEL_BOOTIMAGE_CMDLINE = androidboot.hardware=qcom androidboot.memcg=1 lpm_levels.sleep_disabled=1 service_locator.enable=1 androidboot.usbcontroller=a600000.dwc3 swiotlb=0 loop.max_part=7 cgroup.memory=nokmem,nosocket pcie_ports=compat iptable_raw.raw_before_defrag=1 ip6table_raw.raw_before_defrag=1 console=tty0 droidian.lvm.prefer

DEVICE_VENDOR = nothing
DEVICE_MODEL = spacewar
DEVICE_PLATFORM = sm7325
DEVICE_FULL_NAME = Nothing Phone (1)

# This kernel is built from a fragment stack rather than a single defconfig:
#   gki_defconfig
#   + vendor/lahaina_ALLYES_GKI.config   (GENERATED at build time, see rules)
#   + vendor/lahaina_QGKI.config
#   + vendor/debugfs.config
#   + vendor/halium.config
# Droidian's own Halium/Droidian/pstore fragments come from common_fragments.
KERNEL_CONFIG_USE_FRAGMENTS = 1
KERNEL_CONFIG_USE_DIFFCONFIG = 0
KERNEL_DEFCONFIG = gki_defconfig

KERNEL_IMAGE_WITH_DTB = 1
KERNEL_IMAGE_WITH_DTB_OVERLAY = 0

# deviceinfo_flash_* / deviceinfo_bootimg_*
KERNEL_BOOTIMAGE_PAGE_SIZE = 4096
KERNEL_BOOTIMAGE_BASE_OFFSET = 0x00000000
KERNEL_BOOTIMAGE_KERNEL_OFFSET = 0x00008000
KERNEL_BOOTIMAGE_INITRAMFS_OFFSET = 0x01000000
KERNEL_BOOTIMAGE_TAGS_OFFSET = 0x00000100
KERNEL_BOOTIMAGE_DTB_OFFSET = 0x01f00000

KERNEL_BOOTIMAGE_PATCH_LEVEL = 2022-11
KERNEL_BOOTIMAGE_OS_VERSION = 11
# Header v3 (deviceinfo_bootimg_header_version=3) — v3 drops the per-image
# address fields and requires a separate vendor_boot image.
KERNEL_BOOTIMAGE_VERSION = 3
KERNEL_INITRAMFS_COMPRESSION = gz
KERNEL_BOOTIMAGE_GENERATE_VENDOR_BOOT = 1
KERNEL_BOOTIMAGE_VENDOR_CMDLINE =

DEVICE_VBMETA_REQUIRED = 1
DEVICE_VBMETA_IS_SAMSUNG = 0

FLASH_ENABLED = 1
FLASH_IS_AONLY = 0
FLASH_IS_LEGACY_DEVICE = 0
FLASH_IS_EXYNOS = 0
FLASH_USE_TELNET = 0
FLASH_INFO_MANUFACTURER = Nothing
FLASH_INFO_MODEL = Spacewar
FLASH_INFO_CPU = Qualcomm Technologies, Inc SM7325
FLASH_INFO_DEVICE_IDS = spacewar

# Android 11 device -> clang-android-10.0-r370808 per the porting guide.
BUILD_CROSS = 1
BUILD_TRIPLET = aarch64-linux-gnu-
BUILD_CLANG_TRIPLET = aarch64-linux-gnu-
BUILD_CC = clang
BUILD_LLVM = 1
BUILD_SKIP_MODULES = 0
CLANG_VERSION = 10.0-r370808
CLANG_CUSTOM = 0
BUILD_PATH = /usr/lib/llvm-android-$(CLANG_VERSION)/bin
DEB_TOOLCHAIN = linux-initramfs-halium-generic:arm64, binutils-aarch64-linux-gnu, gcc-4.9-aarch64-linux-android, g++-4.9-aarch64-linux-android, libgcc-4.9-dev-aarch64-linux-android-cross
DEB_BUILD_ON = amd64
DEB_BUILD_FOR = arm64
KERNEL_ARCH = arm64
KERNEL_BUILD_TARGET = Image.gz
