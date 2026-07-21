# Build artifacts

Droidian kernel packages for the Nothing Phone (1) (spacewar), built from
`jaylfc/linux-android-nothing-spacewar@droidian-taos`.

`linux-bootimage-*.deb` contains the flashable set:

- `boot.img` — Android boot image, header v3 (21MB kernel + 16MB Droidian initramfs)
- `vendor_boot.img` — required for header v3
- `vbmeta.img`
- `recovery.img` — UBports/Droidian recovery
- `flash-bootimage/*.conf` — flash metadata

Built on the Fedora build host (12-core, native amd64) in Droidian's
`build-essential` container. The `.deb` here is kept as a checkpoint; the
canonical build is the CI/Fedora pipeline against the kernel repo.
