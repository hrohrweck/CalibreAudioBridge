# TTS engines

All engines implement the same `TTSEngine` protocol and are selected per book
language via the `tts.routing` table; engines are tried in order and the first
available one narrates.

## Kokoro (default)

* License: Apache-2.0 · ~82M params · English, German and 6 more languages.
* Fixed voice catalog (`af_heart`, `df_anna`, …) — no cloning needed.
* CPU-friendly (CoreML/MLX acceleration where available).

Install:

```
uv sync --extra kokoro      # installs kokoro + soundfile (pulls torch CPU)
```

First use downloads the model from Hugging Face (`hexgrad/Kokoro-82M`).

## Piper (fallback)

* License: MIT (original, now archived) / GPL (maintained `piper1-gpl` fork).
  Invoked as a **separate binary** — its GPL does not extend to CalibreAudioBridge.
* Very fast CPU synthesis; many languages; less expressive than Kokoro.
* Install the `piper` binary and voice `.onnx` models (e.g.
  `en_US-lessac-high.onnx`, `de_DE-thorsten-high.onnx`) into
  `tts.piper_model_dir`.

## LuxTTS (English-only, experimental)

* License: Apache-2.0 · voice **cloning** via a narrator reference WAV (≥3 s).
* Research-grade: no PyPI package, no releases, single maintainer, English only.
  Routed only to `eng` books; falls back to the next engine on failure.
* Install: `git clone https://github.com/ysharma3501/LuxTTS` into the venv and
  set `tts.luxtts.reference_wav`.

## stub (dry runs / tests)

A deterministic no-op engine (`stub`) renders silence — useful to test the full
pipeline end-to-end without any model installed. Route to it explicitly in
`tts.routing` when dry-running.

## Voice/routing configuration

```toml
[tts.voices]
en = "af_heart"
de = "df_anna"

[tts.routing]
en = ["kokoro", "piper"]
de = ["kokoro", "piper"]
default = ["kokoro", "piper", "luxtts"]
```
