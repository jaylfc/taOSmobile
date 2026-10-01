# Notices for the taOS text-to-speech engine

## Model: Kitten TTS nano v0.8 (Apache-2.0)

The speech synthesis model installed by `install-tts.sh` is **Kitten TTS Nano
0.8** (`kitten-tts-nano-0.8-fp32`), created by **KittenML**. The voice used is
`expr-voice-2-m` (speaker id 0).

- Original model: https://huggingface.co/KittenML/kitten-tts-nano-0.8-fp32
  (project: https://github.com/KittenML/KittenTTS). Its model card declares
  `license: apache-2.0`.
- Licence: Apache License, Version 2.0,
  https://www.apache.org/licenses/LICENSE-2.0. The archive below carries the
  standard Apache-2.0 text as `LICENSE`, with the appendix's copyright line
  left as the unfilled template (`Copyright [yyyy] [name of copyright owner]`),
  so no copyright holder is named beyond KittenML as the model's publisher.
- Copy used here: the sherpa-onnx project's repackaging of the upstream
  Hugging Face assets, `kitten-nano-en-v0_8-fp32.tar.bz2`
  (https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models). Its README
  says it was prepared from `KittenML/kitten-tts-nano-0.8-fp32` with that
  project's `scripts/kitten-tts/v0_8/run.sh`, which writes ONNX metadata and
  packs the voice table as `voices.bin`. taOS applies no further changes to the
  weights.
- Pinned file hashes are in `voice/tts/manifest.json` under the controller's
  data dir (on the handset `/root/tinyagentos/data`).

KittenML does not endorse taOS or this use of the model.

## Phonemizer data and code: espeak-ng (GPL-3.0-or-later)

The `espeak-ng-data/` directory installed beside the model, and the espeak-ng
code that reads it, are from eSpeak NG (https://github.com/espeak-ng/espeak-ng),
which "is released under the GPL version 3 or later license". sherpa-onnx
builds espeak-ng (its fork at
https://github.com/csukuangfj/espeak-ng, at the commit and hash pinned in
sherpa-onnx's `cmake/espeak-ng-for-piper.cmake`) and links it statically into
`libsherpa-onnx-c-api.so`, so it runs in the same process as the daemon. The
project owner has reviewed and accepted this for taOS, which is licensed
AGPL-3.0-or-later.

## Inference engine: sherpa-onnx (Apache-2.0)

https://github.com/k2-fsa/sherpa-onnx, built from source by `install-stt.sh`
at the commit recorded in `manifest.json` (one library serves both speech
daemons), linked against the system ONNX Runtime (MIT).
