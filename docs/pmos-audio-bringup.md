# postmarketOS audio bring-up on `spacewar` (Nothing Phone 1)

Status: **root cause narrowed to a 2-property device-tree change.** Not yet applied — it needs a
dtb rebuild and Jay's go-ahead. Measured 2026-09-15 on the device (`taosmobile`, kernel 7.2.2).

## The correction this document exists to record

An earlier diagnosis concluded the pmOS DT was **missing** the speaker backend dai-link, and that
fixing it meant "add a dai-link binding a q6afe I2S port to the two tfa9873 codecs" — a substantial
bring-up. **That was wrong, and the wrongness mattered:** it sized the job as a kernel bring-up when
it is a two-line DT fix.

The dai-link is already present and already correct:

| pmOS DT `/sound/i2s-dai-link` | resolves to |
|---|---|
| `cpu/sound-dai = <0x194 16>` | q6afe dai **16 = `PRIMARY_MI2S_RX`** |
| `codec/sound-dai = <0x196 0>, <0x197 0>` | `i2c@988000/codec@34` and `codec@35` — both tfa9873 amps |
| `platform/sound-dai = <0x195>` | q6asm / q6routing |

`PRIMARY_MI2S_RX` is independently confirmed as the right port by the **stock vendor** mixer file
(see "Provenance"): the stock `speaker` path resets through `PRI_MI2S_RX Audio Mixer MultiMedia{2,3,7,…}`.
So pmOS already points at the same backend the stock firmware uses.

## Actual root cause

Both amps are the same driver instance type, and **neither codec node carries `sound-name-prefix`**
(verified: `codec@34` and `codec@35` each have only `#sound-dai-cells compatible name phandle reg
reset-gpio`). They therefore register identically-named DAPM widgets, and the second clobbers the
first. The kernel says so directly:

```
tfa987x 2-0035: ASoC: sink widget PWUP overwritten
tfa987x 2-0035: ASoC: sink widget Speaker overwritten
MultiMedia1: ASoC: no backend DAIs enabled for MultiMedia1, possibly missing
             ALSA mixer-based routing or UCM profile
```

With the DAPM graph broken the link never comes up as a usable backend, so q6routing never exposes
`PRI_MI2S_RX Audio Mixer MultiMedia1`, and `speaker-test -D hw:0,0` returns `-22 EINVAL`.
Card `NP1` consequently has **18 controls, all HDMI/DisplayPort/jack, and 0 matching `MI2S`.**

## The change to try

Add a distinct prefix to each amp node (names are a choice; they become the control-name prefix):

```dts
&i2c@988000 {
        codec@34 { sound-name-prefix = "EAR";  };   /* earpiece / receiver */
        codec@35 { sound-name-prefix = "MAIN"; };   /* main speaker        */
};
```

Expected result: widgets stop colliding, the i2s-dai-link enumerates, `PRI_MI2S_RX Audio Mixer
MultiMedia1` appears in `amixer controls`, and PipeWire gains a sink.

Which amp is which is corroborated by the stock mixer file: `TFA_CHIP_SELECTOR` takes values 0/1/2
with `TFA Profile` of `speaker` / `receiver` / `Powerdown_All`.

**Unproven until it is built and booted.** It is a hypothesis with a named mechanism, not a result.

## Mic and headphones — still a real bring-up

Unchanged from the earlier diagnosis and **not** addressed by the above: `wcd938x` is entirely
absent from the pmOS DT, so there is no mic and no headphone path. The stock board overlay shows
what the vendor wires up (`lahaina-yupikqrd-snd-card`, `asoc-codec-names` including `wcd938x_codec`,
plus a full `qcom,audio-routing` for AMIC1-5 / DMIC0-5 / HPHL / HPHR / AUX). Fairphone 5
(`fairphone-fp5`) remains the mainline template.

Note `qcom,wsa-max-devs = <0x00>` on the stock card — spacewar has **no WSA SoundWire speakers**,
which is why the generic Qualcomm `mixer_paths.xml` (all WSA) is the wrong reference for this board.

## Board facts worth not re-deriving

- Amp reset is **tlmm GPIO 0x65 (101)**, and stock declares it on `codec@34` **only**. pmOS
  currently sets `reset-gpio` on **both** nodes — a difference, not yet shown to matter.
- The `spkr_1_sd_n` / `spkr_2_sd_n` pinctrl entries (gpio15 / gpio42) that appear in the Qualcomm
  **SoC** dtb are *not* spacewar's amp pins. They are generic reference-design nodes. Do not use them.
- Stock UCM/HAL drives the speaker purely through the vendor tfa98xx driver's own controls
  (`TFA_CHIP_SELECTOR`, `TFA Profile`) — there is no q6afe speaker mixer in the stock path at all.
  The mainline `tfa987x` driver does not expose those controls, so the stock mixer file is a
  **reference for the backend port, not a recipe to replay.**

## Provenance

Reference artefacts are kept **outside this public repo** (proprietary Nothing/Qualcomm content) at
`~/.taos-team/spacewar-audio-refs/`, with `SHA256SUMS`:

- `mixer_paths_yupikqrd.xml` etc. — from the device's own stock `vendor` partition, mounted
  read-only from `/dev/mapper/vendor_a`. Fingerprint `Nothing/Spacewar/Spacewar:11/RKQ1.230824.001`.
- `dtbo_a.img` + `spacewar-board-overlay-o05.dts` — stock board overlay read from `dtbo_a`.
  Identified as spacewar's by its `aw210xx` Glyph LED and Nothing `hardware_id` nodes.
- `tfa98xx.cnt` (3745 B) — the TFA tuning container, previously recorded as missing from
  `/lib/firmware`. **Caveat:** this is the *downstream* driver's container format; whether mainline
  `tfa987x` consumes it is unverified.

Sources: the UBports community port repo Jay supplied
(`gitlab.com/ubports/porting/community-ports/android11/nothing-phone-1/nothing-spacewar`) provided
the SoC dtbs; the decisive board- and vendor-level artefacts came from the phone itself.

Nothing was written to the device: `vendor` was mounted read-only and unmounted, staging removed.
