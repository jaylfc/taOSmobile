# Notices for the taOS speech-to-text engine

## Model: Parakeet-TDT 0.6b v3 (CC-BY-4.0)

The speech recognition model installed by `install-stt.sh` is
**Parakeet-TDT 0.6B v3** (`parakeet-tdt-0.6b-v3`), created by **NVIDIA**.

- Original model: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/
- Copy used here: the int8-quantised ONNX conversion distributed by the
  sherpa-onnx project as
  `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2`
  (https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models). It is an
  adapted form of NVIDIA's model (ONNX export and int8 quantisation done by
  that project, not by NVIDIA and not by taOS). taOS applies no further
  changes to the weights.
- Pinned file hashes are in `voice/stt/manifest.json` under the controller's
  data dir (on the handset `/root/tinyagentos/data`).

NVIDIA does not endorse taOS or this use of the model.

## Inference engine: sherpa-onnx (Apache-2.0)

https://github.com/k2-fsa/sherpa-onnx, built from source at the commit recorded
in `manifest.json`, linked against the system ONNX Runtime (MIT).
