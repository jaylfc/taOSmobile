# postmarketOS audio bring-up on `spacewar` (Nothing Phone 1)

Measured 2026-09-15 against the device (`taosmobile`, `linux-postmarketos-qcom-sc7280` 7.2.2) and
against upstream. **Not applied.** Read the "Prior art" section before doing anything: most of this
problem is already solved by someone else and the remaining delta is small.

## Hardware correction

**There is no headphone jack.** Headphones are Bluetooth, and Bluetooth audio already works. So the
missing WCD9385 costs us the **microphone** (and USB-C wired audio), not headphones. Earlier notes
in this repo that paired "mic + headphones" as the wcd938x deliverable were wrong about the
headphone half.

The two speakers are **top** (`codec@34`) and **bottom** (`codec@35`) — a stereo pair, not
"earpiece + main". Stale `/* EAR */` and `/* SPK */` comments in the DTS say otherwise; upstream has
since relabelled them.

## Prior art — this is largely solved, upstream, already

`sc7280-mainline/linux` **PR #29, "Draft: 7.0.y spacewar audio"** (open, by `f-izzo`, a co-maintainer
of the pmOS kernel package). In his own words:

> I worked on enabling audio on `nothing-spacewar` on and off for more than three months and I got
> tired of it. I will now move to something else. […] **Speaker/Earpiece work with stereo sound.
> WCD codec enumerates but gives no sound (mic/headphones).**

So the speaker problem has a known, working solution; the mic problem is open even for the person who
spent three months on it. No released build has working audio — public reports agree audio is broken,
and the pmOS wiki page is behind Anubis and could not be read (HTTP 200 returning only the challenge
page, not the article).

Related precedent: Fairphone 5 has two amps at the same I2C addresses (`0x34`/`0x35`, Awinic AW88261
rather than TFA), and Luca Weiss's commit adding them uses `sound-name-prefix = "Amplifier L"` /
`"Amplifier R"`. Same board-level problem, same shape of fix.

## What is actually wrong here, and the correction to an earlier diagnosis

An earlier diagnosis in this repo concluded the pmOS DT was **missing** the speaker backend dai-link,
making the fix a kernel bring-up. **That was wrong.** The link is present and correct:

| `/sound/i2s-dai-link` | resolves to |
|---|---|
| `cpu/sound-dai = <0x194 16>` | q6afe dai **16 = `PRIMARY_MI2S_RX`** |
| `codec/sound-dai = <0x196 0>, <0x197 0>` | `codec@34` and `codec@35` — both tfa9873 amps |
| `platform/sound-dai = <0x195>` | q6asm / q6routing |

`PRIMARY_MI2S_RX` is independently confirmed as the right port by the stock vendor mixer file.

The real defect is that **neither codec node carries `sound-name-prefix`.** `tfa9872.c` registers
`Speaker` and `PWUP` as *component* DAPM widgets (`snd_soc_component_driver.dapm_widgets`), so two
un-prefixed instances collide; ASoC applies `sound-name-prefix` to exactly those widgets. The kernel
reports the collision and then the consequence:

```
tfa987x 2-0035: ASoC: sink widget PWUP overwritten
tfa987x 2-0035: ASoC: sink widget Speaker overwritten
MultiMedia1: ASoC: no backend DAIs enabled for MultiMedia1
```

The DAPM graph breaks before any backend enumerates, which is what leaves card `NP1` with 18
HDMI/DisplayPort-only controls and 0 matching `MI2S` — the state that was misread as a missing link.

**`sound-name-prefix` is necessary but not sufficient.** PR #29 also sets `sound-channel`, which is
what gives real L/R stereo rather than both amps playing the same content.

## The change, as upstream actually wrote it

```dts
tfa9873_l: codec@34 {          /* Top speaker */
        sound-channel = <0>;
        sound-name-prefix = "Amplifier L";
};

tfa9873_r: codec@35 {          /* Bottom speaker */
        sound-channel = <1>;
        sound-name-prefix = "Amplifier R";
};
```

The 7.2.2 driver already supports `sound-channel` (`ab0ee5f6d sound: codecs: tfa9872: pass channel
index`), so no driver patch is needed for this part.

**PR #29's two reverts are not needed on 7.2.y.** It reverts the FP5 commits "Increase MI2S BCLK rate
for 32-bit playback" and "Advertise S32 format to ALSA while sending 24-bits", which broke spacewar.
Checked by reading the 7.2.y sources rather than trusting a commit search: `sm8250.c` still has a
fixed `MI2S_BCLK_RATE 1536000`, and `q6asm-dai.c` contains no `S32` at all. Neither commit is in this
tree. (Izzo predicted they would stop being necessary once Val Packett's driver fix landed; it has.)

**Still unproven:** that the DTS change alone is enough on 7.2.2. Izzo's result was on 7.0.y *with*
the reverts. The mechanism is confirmed at source level; the outcome is not, until it is booted.

## Test loop — it DOES need a boot-partition write (claim retracted)

**An earlier version of this document said "no flashing required". That was wrong, and it was tested
and disproved on 2026-09-15.** Recording the disproof because the reasoning was superficially sound.

The mistake: `/boot` is a writable filesystem holding `sm7325-nothing-spacewar.dtb`, and
`/boot/loader/entries/pmos.conf` names that file as `devicetree`. It looks like an editable UEFI
boot. **It is not.** `/boot` is **ext2**, which UEFI firmware cannot read, so those files are staging
inputs, not what the firmware loads. The dtb is baked into `boot.img` (it contains `nxp,tfa9873`)
which is flashed to the boot partition, and that is what the kernel actually uses.

Measured proof: the patched dtb was installed to `/boot` and the device rebooted cleanly, but
`/sys/firmware/devicetree/base/.../codec@34/sound-name-prefix` **did not exist**, the control count
stayed at 18, and dmesg still showed both `widget overwritten` lines. The edit was inert.

**The device is running slot `_b`** (`androidboot.slot_suffix=_b` in `/proc/cmdline`), so the flash
target is `boot_b`, **not** `boot_a`. Backing up `boot_a` — the obvious-looking name — backs up the
*inactive* slot and would give false confidence in a recovery path.

So the real loop is: edit `/boot/<dtb>`, run `boot-deploy` to regenerate `boot.img`, write it to the
**active** slot, reboot. `deviceinfo_flash_method="fastboot"`, `deviceinfo_generate_bootimg="true"`.
Recovery is fastboot with a backup of the active slot; the bootloader is unlocked. Still nowhere near
`xbl`/`abl`.

Note `/tmp` on the device is tmpfs in RAM — do not stage 100 MB partition dumps there.

## Microphone — genuinely open

`wcd938x` is absent from the DT. PR #29 adds it (+205 lines: `wcd9385` node, SoundWire `swr0`/`swr1`,
`lpass_rx_macro`/`lpass_tx_macro`, and `wcd-playback` / `wcd-capture` dai-links on
`RX_CODEC_DMA_RX_0` / `TX_CODEC_DMA_TX_3`), plus a driver change setting min channels = 1 on the
sm8250 TX dai, without which "the record stream never starts". Even with all that, his result is
"enumerates but gives no sound". **Do not expect a quick win here.**

## Provenance and one downgraded claim

Reference artefacts are kept **outside this public repo** (proprietary Nothing/Qualcomm content) at
`~/.taos-team/spacewar-audio-refs/` with `SHA256SUMS`: stock `mixer_paths_yupikqrd.xml` and friends,
`dtbo_a.img`, and the decompiled board overlay. They came from the device's own stock partitions —
`vendor` was mounted **read-only** from `/dev/mapper/vendor_a` and unmounted afterwards, and nothing
was written to the device. Stock fingerprint: `Nothing/Spacewar/Spacewar:11/RKQ1.230824.001`.

**`tfa98xx.cnt` (3745 B) is not usable.** It was previously recorded as a valuable recovery — the
TFA tuning container missing from `/lib/firmware`. The mainline `tfa9872.c` driver contains **zero**
firmware-loading code (no `request_firmware`, no `firmware-name`), so it cannot consume this blob.
It is a downstream artefact only, kept for reference. By contrast FP5's AW88261 *does* take a
`firmware-name`, which is why the two devices are not symmetrical here.

Other board facts worth not re-deriving: the amp reset is **tlmm GPIO 0x65 (101)**, declared by stock
on `codec@34` only, while pmOS sets `reset-gpio` on both. The `spkr_1_sd_n`/`spkr_2_sd_n` pinctrl
entries (gpio15/gpio42) visible in Qualcomm's **SoC** dtb are generic reference-design nodes and are
**not** this board's amp pins. Stock drives the speaker purely through the vendor driver's own
`TFA_CHIP_SELECTOR` / `TFA Profile` controls, which mainline does not expose — so the stock mixer
file is a reference for the backend port, not a recipe to replay.

## Route (a): the change as a kernel-package patch — the durable form

The quick route (edit `/boot/<dtb>`, `boot-deploy`, flash) does not survive a kernel upgrade. The
durable form is a patch carried by the pmOS kernel package, which is what this section records.

On the build host, `device/community/linux-postmarketos-qcom-sc7280/APKBUILD` (pmaports, kernel
7.2.2, tag `v7.2.2-sc7280`) carried **no patches at all** before this. The change adds one:

```
source="
	https://github.com/sc7280-mainline/$_repo/archive/refs/tags/$_tag/$_repo-$_tag.tar.gz
	$_config
	0001-arm64-dts-qcom-sm7325-nothing-spacewar-name-and-chann.patch
"
```

The patch touches `arch/arm64/boot/dts/qcom/sm7325-nothing-spacewar.dts` only, adding
`sound-channel` and `sound-name-prefix` to `tfa9873_l: codec@34` and `tfa9873_r: codec@35` and
correcting the stale `/* EAR */` / `/* SPK */` comments to top/bottom speaker. Upstream already
labels the nodes `_l` and `_r`, so the labels needed no change.

Both patch anchors were asserted to match **exactly once** before substitution rather than
replaced blind — a silent zero-match or double-match is the failure mode that produces a patch
that applies cleanly and changes nothing.

## Verifying the dtb without the device

The patched dtb can be produced and checked entirely off-device, which is worth doing because a
dtb staged on the phone by an earlier session is otherwise unverified:

```
dtc -I dtb -O dts -o pristine.dts sm7325-nothing-spacewar.dtb.pristine
fdtput -t s out.dtb /soc@0/geniqup@9c0000/i2c@988000/codec@34 sound-name-prefix "Amplifier L"
fdtput -t u out.dtb /soc@0/geniqup@9c0000/i2c@988000/codec@34 sound-channel 0
```

(and the same for `codec@35` with `"Amplifier R"` / `1`). **Read the properties back out of the
binary with `fdtget`**, then decompile both dtbs and diff them — the diff must be exactly the four
added lines and nothing else. Writing a property and not reading it back is how a typo'd node path
becomes a silent no-op: `fdtput` will happily create a node that the kernel never looks at.

**Do not compare two independently patched dtbs by sha256.** The local build here is
`0921a547…` while the copy staged on the phone is `6c42fbe7…`; the difference is padding/growth
from a different patching method, not a difference in content. The meaningful comparison is the
decompiled diff, not the hash. A hash is only good for confirming a file did not change in
transit — which is what `SHA256SUMS` in `~/.taos-team/spacewar-audio-refs/` is for.

## Still not proven

The build produces a kernel package; it does not prove audio works. The outcome is unknown until a
boot.img carrying the new dtb is flashed to the **active** slot and the device boots. The checks
that decide it, in order: `sound-name-prefix` present under
`/sys/firmware/devicetree/base/.../codec@34`, the `widget overwritten` lines **gone** from dmesg,
control count above 18 with `MI2S` among them, and only then `speaker-test`. The first three are
what distinguish "the change landed and did not work" from "the change never landed" — the exact
ambiguity that wasted the first attempt.

## Result of the first real test — 2026-09-15, patched kernel booted and measured

The 4-property DTS patch was built as `linux-postmarketos-qcom-sc7280-7.2.2-r1`, flashed, and
booted. **The widget collision is fixed. Audio still does not work.** Both halves matter.

**Fixed, measured on the running device:**

- `sound-name-prefix` is live in the DT: `codec@34` = `Amplifier L`, `codec@35` = `Amplifier R`,
  with `sound-channel` 0 and 1.
- The two `ASoC: sink widget PWUP/Speaker overwritten` lines are **gone** from dmesg. Confirmed
  against a control (`grep -c "Linux version"` = 1) so that the zero is a real absence and not an
  unreadable `dmesg` — `sudo dmesg` without `-S` returns nothing and reads as a clean result.

**Not fixed:**

- Still exactly 18 mixer controls, still only HDMI (8) + `DISPLAY_PORT_RX` (8) + the two jacks.
  **No `Amplifier L/R` controls and no `MI2S` mixer appear.**
- `speaker-test -D hw:0,0` still fails with `Playback open error: -22`, and the kernel says
  `MultiMedia1: ASoC: no backend DAIs enabled for MultiMedia1, possibly missing ALSA mixer-based
  routing or UCM profile`.
- PipeWire still lists zero sinks and zero sources. `qcom-q6afe: Unknown cmd 0x100f6` persists.

So `sound-name-prefix` was **necessary but not sufficient**, exactly as predicted above. Removing
the DAPM collision did not cause the `PRIMARY_MI2S_RX` backend to register with q6routing: the
per-backend mixers that q6routing creates exist for HDMI and DisplayPort but not for MI2S. The next
question is therefore why the `i2s-dai-link` backend does not register, not anything about the amps
— both amps probe, both are prefixed, neither is routed to.

## The boot hang, and what actually caused it

Installing the r1 apk **on the device** produced a boot that hung after `pmos_continue_boot` with no
visible error. The same kernel and the same dtb, packaged into `boot.img` by `pmbootstrap flasher
flash_kernel` on the build host, **boots fine.** So the hang was caused by the on-device
`boot-deploy`/`mkinitfs` path, not by the DTS change.

This was only distinguishable because the two routes build `boot.img` differently. Rolling back
would have hidden it: the device would have booted and the patch would have been blamed.

**`pmbootstrap flasher flash_kernel` flashes what is installed in the rootfs chroot, and it
upgrades that chroot from the local package repo first.** The chroot held r0 before the flash and
r1 after, and its dtb went from 147967 to 148079 bytes. So the "recovery" flash actually shipped the
*patched* kernel. That was lucky rather than intended — verify the chroot's version before treating
this command as a rollback, because it is not one while a newer local package exists.

**Two ways in, and the second one saved this session.** Tailscale took minutes to reconnect after
boot, but pmOS brings up USB networking: the phone is `172.16.42.1` from the build host. When the
tailnet is down, that path still works.

## Correction — the backend was registered all along; the instrument was broken (2026-09-15, later)

**Speakers work.** Measured on the device with the r1 kernel already flashed: stereo playback through
`speaker-test -D plughw:0,0` and, after the UCM fix below, through PulseAudio
(`alsa_output.platform-sound.HiFi__Speaker__sink`, default sink, audible).

**Everything in the two sections above that says "18 controls", "no `MI2S` mixer" or "the
`PRIMARY_MI2S_RX` backend never registers with q6routing" was wrong, and wrong for one reason:**
`amixer -c 0` opens `sysdefault:0`, and on this card that listing aborts at element 19 with
`snd_hctl_elem_info error: No such file or directory`. Everything after `DISPLAY_PORT_RX` was cut off.
`amixer -D hw:0 controls` returns **1050** controls, including `PRI_MI2S_RX Audio Mixer MultiMedia1..8`
(numids 75–82). The kernel side was complete: `q6routing.c` names the widget `PRI_MI2S_RX Audio Mixer`
(the `PRIMARY_MI2S_RX` spelling is only the DT-binding constant), `q6afe-dai.c` has the `PRI_MI2S_RX`
AIF widget, debugfs shows both on the card, and `sm8250.c` never sets `disable_route_checks`, so a
failed route would have failed the whole card bind. The `-22` from `speaker-test` was the ordinary
DPCM state with no mixer switch on — precisely what a UCM profile exists to set.

**Why the listing broke:** the shipped UCM profile includes `/lib/ctl-remap.conf`, which wraps
`ctl.default` in alsa-lib's `remap` plugin. With it, the `default:0`/`sysdefault:0` control listing
stops at 20 lines; with that one include removed, the same command lists all 1050. PulseAudio opens
the mixer through the same `default:${CardId}` path. ~20 upstream profiles include the same file, so
this is either spacewar-specific or an alsa-lib edge case — not yet root-caused, only measured.

**Why PulseAudio had no sink:** pmOS ships `alsa-ucm-conf-qcom-sc7280`, which already carries
`ucm2/Nothing/spacewar/{NP1,HiFi}.conf` (upstream `sc7280-mainline/alsa-ucm-conf`, byte-identical).
It was written for the PR #29 kernel: its `BootSequence` sets `HPHL Volume`/`ADC1 Volume` and it
includes `codecs/wcd938x/init.conf`, none of which exist without wcd938x, so `snd_use_case_mgr_open`
fails and `module-alsa-card` logs *"Failed to find a working profile"*. Note the daemon that owns the
card here is **PulseAudio** (`/etc/xdg/autostart/pulseaudio.desktop`), not WirePlumber — WirePlumber
runs, but only hosts the loopback filters.

**The fix** is `pmos/ucm2/Nothing/spacewar/`: the same profile with every wcd938x-dependent part
wrapped in `If … { Condition { Type ControlExists Control "name='HPHL Volume'" } }`, so it opens on the
shipped kernel and regains headphones/mics automatically when the codec lands; and the `ctl-remap`
include dropped. Verified: `alsaucm -c NP1 set _verb HiFi list _devices` → only `Speaker`; PA
`Active Profile: HiFi`; listing 1050; playback audible. Installed on the phone by copying over the
package's files (originals in `~/ucm-backup/` there) — **an `apk upgrade` of
`alsa-ucm-conf-qcom-sc7280` will overwrite it** until the change is upstream and repackaged.

**Retained lesson:** a control count was read as a property of the kernel when it was a property of
the tool. The tell was there the whole time — the `snd_hctl_elem_info` error line printed *above* the
count — and the fix was one flag (`-D hw:0`). Read the error line before the number.

## Why there were two outputs, and the one-device landscape swap (2026-09-15, evening)

Jay saw two selectable outputs that sounded the same: *Built-in Audio Speaker & Earpiece Playback*
(channels reversed) and *Speakers* (correct). They were the same hardware twice. The first is the
real ALSA sink from the UCM `Speaker` device, whose upstream comment says "Speaker & Earpiece"
because on the Phone (1) the top amplifier (`codec@34`, `Amplifier L`, `sound-channel 0`) *is* the
earpiece: there is no separate earpiece transducer, Android drives the top speaker alone for calls.
The second was a `module-remap-sink` layered on the first to swap L/R for the fixed landscape
mount. The amps expose no per-amp mixer controls (`amixer -D hw:0 controls | grep -i amp` is empty),
so an earpiece-only device would need its own PCM routing and is not something the kiosk needs.

**Now there is one device**, swapped at the ALSA layer: `pmos/landscape-swap.patch` applied over
`pmos/ucm2/` declares a `route` PCM `spacewar_landscape` (ttable 0↔1) in `NP1.conf` and points the
`Speaker` device's `PlaybackPCM` at it, comment "Speakers". The Pulse drop-in
`/etc/pulse/default.pa.d/10-landscape-swap.pa` is gone (kept disabled in `~/ucm-backup/` on the
phone). PulseAudio shows a single `Built-in Audio Speakers` sink; `speaker-test -D pulse -t wav`
plays through it. Three things cost an hour and are worth keeping:

- **PulseAudio never consults `/etc/asound.conf` for UCM PCMs.** It opens them inside a private
  namespace (`_ucm0001.<name>`), so the PCM must be declared in the UCM profile via a top-level
  `LibraryConfig.<id>.Config { pcm.<name> { … } }` block (the Fairphone 5 profile is the example).
  `${CardId}` is **not** substituted inside that block (`Cannot get card index for ${CardId}`); use
  the card name, `hw:NP1,0`.
- **The route slave's `format` must be pinned.** Left unset, the route plugin negotiates hw params
  the q6asm PCM rejects, and a rejected `hw_params` leaves the DSP stream registered so *every* later
  open fails `EINVAL`, including plain `hw:0,0`, with no dmesg line. Recovery is toggling
  `PRI_MI2S_RX Audio Mixer MultiMedia1` off and on (the UCM verb does that on the next PulseAudio
  start). Related: `hw:0,0` cannot be opened at all while that mixer route is 0 (`Routing not setup
  for MultiMedia-1 Session`), so any test with PulseAudio stopped must set it to 1 first.
- **PulseAudio respawns itself** here (D-Bus/autospawn under `systemd --user`; there is no
  `pulseaudio.service`), so `pkill` alone never gives a clean daemon. Drop
  `autospawn = no` into `~/.config/pulse/client.conf` for the duration of a test and remove it after.

Still open from the same log: PulseAudio warns `Invalid CTL default:NP1 … Failed to find a working
mixer device`. With the `ctl-remap` include dropped (upstream PR #9, commit 2) nothing defines
`ctl.default` in the UCM namespace, so `PlaybackMixer "default:${CardId}"` resolves to nothing and
volume is software-only. Harmless today (the amps have no volume controls to bind anyway) but the PR
should probably switch the mixer to `hw:${CardId}` or say so.
