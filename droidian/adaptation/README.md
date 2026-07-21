# adaptation-nothing-spacewar

Droidian device adaptation for the Nothing Phone (1) (spacewar, SM7325).

Two packages:
- `adaptation-nothing-spacewar` — metapackage; depends on
  `linux-bootimage-nothing-spacewar`, `adaptation-hybris-api30-phone`
  (Halium 11 = api30), and the configs package.
- `adaptation-nothing-spacewar-configs` — device-node udev rules
  (`etc/udev/rules.d/70-spacewar.rules`, Qualcomm pattern) and hostname.

**Minimum-viable-to-boot tier.** Enough to reach a shell with correct /dev
permissions; camera/haptics/notch/audio tuning are later polish (see
`docs/droidian-port-plan.md`). The udev rules are based on Droidian's Qualcomm
`miatoll` rules and need refining against a live `ls -l /dev` during bring-up.

`adaptation-hybris-api30-phone` must match the Halium level the kernel was
built against (Halium 11 → api30). A mismatch breaks the whole HAL stack.
