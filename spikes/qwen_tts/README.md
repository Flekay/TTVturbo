# Qwen3-TTS Voice-Clone Runtime Spike

Isolated spike that proves a real, local voice-clone run with
`Qwen/Qwen3-TTS-12Hz-1.7B-Base` works on the target hardware
(NVIDIA RTX 5070, 12 GB VRAM, 64 GB RAM, Windows).

**Status: `REAL_MODEL VERIFIED`** - three real generations were executed on
the RTX 5070; see `REPORT.md` for full measurements.

This is **not** a product integration. It only lives under `spikes/qwen_tts/`
and must not change any other part of the TTVturbo repository.

## Layout

```
spikes/qwen_tts/
├── clone.py          # CLI entrypoint: real model run
├── runtime.py        # model load, attention backend probe, VRAM/RAM metrics, teardown
├── diagnostics.py    # input + output validation (no model needed)
├── requirements.txt  # spike-only dependencies
├── README.md         # this file
├── REPORT.md         # honest status + measured results from the hardware run
├── tests/
│   ├── conftest.py
│   └── test_validation.py
└── output/
    └── .gitkeep      # generated WAVs are git-ignored, never committed
```

## Install

Use a clean Python 3.12 environment (upstream recommendation):

```powershell
python -m venv .venv-qwen-tts
.venv-qwen-tts\Scripts\activate
pip install -U -r spikes/qwen_tts/requirements.txt
```

`flash-attn` is intentionally not pinned: on RTX 50-series (Blackwell)
prebuilt wheels are unreliable. The runtime probes `flash_attention_2` and
falls back to `sdpa` automatically, documenting the choice in the metrics
file. A flash-attn failure never produces a fake result.

## Real run

```powershell
python spikes/qwen_tts/clone.py `
  --ref-audio recordings/example.wav `
  --ref-text "Der exakte Text der Referenzaufnahme." `
  --text "Dies ist ein neu erzeugter Satz mit derselben Stimme." `
  --language German `
  --output spikes/qwen_tts/output/test.wav
```

This runs the real pipeline:

```
Modell laden
-> Referenz-WAV validieren
-> create_voice_clone_prompt()
-> generate_voice_clone()
-> Ergebnis als WAV speichern
-> WAV erneut oeffnen und validieren
```

A `test.wav.metrics.json` is written next to the output containing real
measurements: model revision, attention backend + fallback note, load /
prompt / generation timings, peak VRAM/RAM, sample rate, output SHA-256
and the four VRAM snapshots (before load, peak load, peak generation,
after release).

Exit codes:

* `0` - `REAL_MODEL_VERIFIED` (a brand-new, non-silent, non-identical WAV was produced)
* `1` - model or output validation failed (`FAILED_ON_HARDWARE` / `FAILED_OUTPUT_VALIDATION`)
* `2` - input validation failed (`FAILED_INPUT_VALIDATION`)

## Tests

No-model unit tests (never download the 1.7B weights):

```powershell
pytest spikes/qwen_tts/tests -v
```

Real end-to-end test (downloads weights, needs the GPU):

```powershell
$env:TTVTURBO_RUN_QWEN_TTS_E2E="1"
pytest spikes/qwen_tts/tests -v
```

The e2e test only passes if a brand-new WAV file was actually produced by
the model (not a stub, not a copied reference).

## What gets committed

Only source files under `spikes/qwen_tts/`. Model weights and generated
audio are git-ignored via the repo `.gitignore` and must never be committed.
