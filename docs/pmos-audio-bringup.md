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

## Test loop — no flashing required

**Correction to an earlier plan that called for a dtb rebuild and a partition flash.** That is not
how this device boots and not what is needed.

`/boot` is a writable ext2 filesystem (loop over the real block device `/dev/sda10`) using
systemd-boot, and `/boot/loader/entries/pmos.conf` names the device tree as a plain file:

```
linux linux.efi
devicetree sm7325-nothing-spacewar.dtb
```

So the loop is: **back up `/boot/sm7325-nothing-spacewar.dtb`, decompile it, add the four properties,
recompile, reboot.** No `pmbootstrap`, no kernel rebuild, no write to `boot`/`dtbo`, nothing near
`xbl`/`abl`. Reverting is restoring one file. A maintainer building from source would instead patch
the DTS and rebuild the kernel package, but for *testing the hypothesis* the file swap is equivalent
and far cheaper.

Caveat: `deviceinfo_flash_kernel_on_update="true"`, so a kernel package upgrade will overwrite the
edited dtb. This is a test, not a durable fix — the durable fix is the DTS change upstream.

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
