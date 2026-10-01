# Notices for the taOS text-to-speech engine

taOS is licensed AGPL-3.0-or-later.

## Voice model: Piper en_GB-cori-high (public domain)

The voice installed by `install-tts.sh` is **en_GB-cori-high** ("cori", UK
English, female, single speaker, 22,050 Hz), a Piper voice. Its MODEL_CARD
declares the dataset **LibriVox** (https://librivox.org) with `License: public
domain`. The voice was trained from scratch by **Bryce Beattie**
(https://brycebeattie.com/files/tts/), who assembled the dataset (about 24
hours of LibriVox recordings). Credit is given here although public domain does
not require it. Beattie and LibriVox do not endorse taOS or this use.

- The voice was trained with the Piper toolkit (originally rhasspy/piper, now
  OHF-Voice/piper1-gpl, GPL-3.0). **No piper1-gpl code is in taOS or in the
  binary that runs the voice**: the model is only a data file (`.onnx` weights,
  `.onnx.json`, `tokens.txt`).
- Copy used: the sherpa-onnx project's packaging,
  `vits-piper-en_GB-cori-high.tar.bz2`
  (https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models), unchanged.
  The archive and each file are pinned by sha256 in `install-tts.sh`; the
  installed hashes are in `voice/tts/manifest.json` under the controller's data
  dir (on the handset `/root/tinyagentos/data`).

## Voice licence allowlist

`install-tts.sh` refuses any voice whose MODEL_CARD `License:` is not public
domain, CC0 or CC BY (it also refuses a card that states a non-commercial or
research restriction anywhere). A voice that is not on the allowlist is not
installed, whatever its quality. For example these are refused:
`en_US-lessac` (Blizzard 2013 licence, research only), `en_US-ryan`, and
`hfc_female` / `hfc_male` (CC BY-NC-SA). Any CC BY voice added later must have
its dataset credited in this file.

## Inference engine: sherpa-onnx (Apache-2.0)

https://github.com/k2-fsa/sherpa-onnx, built from source by `install-stt.sh`
at the commit recorded in `manifest.json` (one library serves both speech
daemons), linked against the system ONNX Runtime (MIT). The voice runs through
sherpa-onnx's own VITS runtime.

## Phonemizer: espeak-ng (GPL-3.0-or-later)

The one GPL-3.0 component that actually runs is **eSpeak NG**
(https://github.com/espeak-ng/espeak-ng), which "is released under the GPL
version 3 or later license": its code, and the `espeak-ng-data/` directory
installed beside the voice. sherpa-onnx builds espeak-ng (its fork at
https://github.com/csukuangfj/espeak-ng, at the commit and hash pinned in
sherpa-onnx's `cmake/espeak-ng-for-piper.cmake`) and links it statically into
`libsherpa-onnx-c-api.so`, so it runs in the same process as the daemon. The
project owner has reviewed and accepted this for taOS, which is licensed
AGPL-3.0-or-later.
