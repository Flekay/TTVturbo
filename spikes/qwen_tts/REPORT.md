# Qwen3-TTS Voice-Clone Spike - Status Report

## Status

`REAL_MODEL VERIFIED`

The implementation is complete, all 16 unit tests pass, and three real
generations were executed on the target hardware using
`Qwen/Qwen3-TTS-12Hz-1.7B-Base`. Each produced a brand-new, non-silent,
non-identical WAV file. The gated e2e test (`TTVTURBO_RUN_QWEN_TTS_E2E=1`)
also passes.

## Hardware target

* GPU: NVIDIA GeForce RTX 5070 (12 GB VRAM, Blackwell)
* RAM: 64 GB
* OS: Windows
* Python: 3.12.10

## Installed package versions

| Package | Version |
| --- | --- |
| qwen-tts | 0.1.1 |
| torch | 2.11.0+cu128 |
| transformers | 4.57.3 |
| accelerate | 1.12.0 |
| soundfile | 0.14.0 |
| numpy | 2.4.6 |
| psutil | 7.2.2 |

Note: `pip install -r spikes/qwen_tts/requirements.txt` pulled a CPU-only
torch from the default PyPI index. The CUDA build
(`torch==2.11.0+cu128` from the `cu128` index) had to be reinstalled
afterwards. The product setup must pin the CUDA wheel explicitly.

## Model + attention

* Model id: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
* Model revision: `fd4b254389122332181a7c3db7f27e918eec64e3`
* Device: `cuda:0`
* Dtype: `bfloat16`
* Attention backend: `sdpa`
* Fallback note: `flash_attention_2 unavailable (ModuleNotFoundError: No module named 'flash_attn'); using sdpa`

`flash-attn` is not installed. On RTX 50-series (Blackwell) prebuilt
`flash-attn` wheels are unreliable, so `sdpa` is the practical backend.
The runtime probes `flash_attention_2` first and falls back to `sdpa`
automatically; a flash-attn failure never produces a fake result.

## Reference audio

A synthetic German reference was generated with the Windows SAPI voice
`Microsoft Hedda Desktop [de-DE]` so the exact transcript is known:

* File: `spikes/qwen_tts/output/reference_de.wav`
* Duration: 8.19 s (inside the recommended 5-12 s window)
* Sample rate: 22050 Hz, mono
* Transcript: "Hallo, dies ist eine kurze Referenzaufnahme fuer den Qwen3 Sprachklonungsversuch auf der lokalen Grafikkarte."

The reference WAV and all generated WAVs are git-ignored and not committed.

## Measurements

### Timings (seconds)

| Step | Attempt 1 | Attempt 2 | Attempt 3 |
| --- | --- | --- | --- |
| Model load | 180.01 (incl. first download) | 5.12 (cached) | 5.07 (cached) |
| Prompt creation | 9.84 | 9.84 | 9.84 |
| Generation | 5.44 | 6.37 | 5.03 |
| Output duration | 3.36 | 3.92 | 3.60 |

### Memory

| Metric | Value |
| --- | --- |
| Peak VRAM (allocated) | 4 244 563 456 bytes (~3.95 GB) |
| Peak VRAM (reserved) | 4 731 174 912 bytes (~4.41 GB) |
| Peak RAM (process RSS) | ~2.74 GB |
| VRAM before load | 0 bytes allocated / 12.82 GB total |
| VRAM after release | 9 598 976 bytes allocated (~9.2 MB), 337 641 472 bytes reserved (~322 MB) |

Peak VRAM stays well below the 12 GB limit (headroom ~8 GB). After
`gc.collect()` + `torch.cuda.empty_cache()` + `torch.cuda.ipc_collect()`
the allocated VRAM drops to ~9 MB, but ~322 MB stays reserved by the
CUDA caching allocator. This is expected behaviour and does not indicate
a leaked context.

### Output

| Metric | Value |
| --- | --- |
| Output sample rate | 24000 Hz |
| Output SHA-256 (attempt 1) | `5acef1b81c3ee4ec1f8d0b13f6fcb2bf5243b1279e9a1f6eaf43c7d8da23ec31` |
| Output SHA-256 (attempt 2) | `b85fa32be7d1bf21...` |
| Output SHA-256 (attempt 3) | `23d16311e02eaab4...` |
| Reference SHA-256 | `628cc4d804e5406f...` |

All three outputs differ from each other and from the reference (no
byte-identity, no copied reference). All passed the output validation
(exist, readable, >0.5 s, no NaN/Inf, not fully silent, peak in valid
float range, not byte-identical to reference).

## Three real attempts

| # | Target text (German, <=300 chars) | Output | Status |
| --- | --- | --- | --- |
| 1 | „Das System verarbeitet die Aufnahme vollstaendig lokal." | 3.36 s | REAL_MODEL_VERIFIED |
| 2 | „Die RTX-Grafikkarte uebernimmt die eigentliche Sprachgenerierung." | 3.92 s | REAL_MODEL_VERIFIED |
| 3 | „TTVturbo analysiert Twitch-Clips direkt auf dem Server." | 3.60 s | REAL_MODEL_VERIFIED |

## Heard anomalies / technical errors

* No technical errors during the three runs.
* `sox` binary is not installed on the machine; the `sox` Python package
  prints a non-fatal "SoX could not be found" warning on import. It did
  not affect generation (qwen-tts uses `soundfile`/`librosa` for the
  actual I/O).
* `huggingface_hub` warns that Windows symlinks are disabled in the cache
  (Developer Mode off). Cache still works, just uses more disk space.
  Set `HF_HUB_DISABLE_SYMLINKS_WARNING=1` to silence it.
* The heuristic repetition/silent-prefix check is informational only;
  manual listening is still recommended for production QC. No long silent
  prefix was detected in any of the three outputs.
* Manual listening was not performed as part of this automated spike;
  subjective voice-clone quality (timbre match, pronunciation of the
  English term "TTVturbo"/"Twitch-Clips") must be reviewed by a human
  before product integration.

## Recommended product configuration

Based on the measured results:

* **Model**: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`
* **Device**: `cuda:0`
* **Dtype**: `bfloat16`
* **Attention backend**: `sdpa` (do not require `flash_attention_2` on
  RTX 50-series; treat flash-attn as an optional accelerator only)
* **Language**: `German` (or per-request)
* **x_vector_only_mode**: `false` (full ICL clone for best quality)
* **Reference window**: 5-12 s, 22050+ Hz, mono or stereo
* **Target text limit**: 300 chars per call; split longer scripts
  sentence-by-sentence and reuse one `create_voice_clone_prompt` across
  the batch (the prompt-creation step is ~9.8 s and should not be
  repeated per sentence).
* **VRAM budget**: plan for ~4.5 GB peak (model + generation). The 12 GB
  RTX 5070 has ample headroom; a single GPU can serve the model
  concurrently with other small workloads.
* **Torch wheel**: pin the CUDA build explicitly
  (`torch==2.11.0+cu128` from the `cu128` index). The default PyPI wheel
  is CPU-only on Windows and will silently disable CUDA.

### Separate-process recommendation

**Yes - run the model in a dedicated worker process for product
integration.** Reasons:

1. The clean teardown routine drops allocated VRAM to ~9 MB but ~322 MB
   stays reserved by the CUDA caching allocator inside the process. Only
   terminating the process fully returns all CUDA memory to the OS.
2. A long-lived worker can keep the model resident (5 s warm load vs.
   180 s cold load) and reuse a single `voice_clone_prompt` across many
   requests.
3. Process isolation prevents a model crash or OOM from taking down the
   FastAPI app (`app.py`) and makes memory leaks recoverable by
   restarting the worker.
4. The 12 GB GPU is shared; a worker process with a clear VRAM budget
   makes coexistence with other GPU workloads predictable.

Suggested shape for later (NOT implemented in this spike): a persistent
`qwen_tts_worker` process that loads the model once, accepts
`{ref_audio, ref_text, text, language}` jobs over a queue/socket, and
streams back the WAV + metrics. The spike's `runtime.QwenTTSRuntime` is
the unit that would live inside that worker.

## What was committed

Only source files under `spikes/qwen_tts/`:

```
spikes/qwen_tts/.gitignore
spikes/qwen_tts/README.md
spikes/qwen_tts/REPORT.md
spikes/qwen_tts/clone.py
spikes/qwen_tts/diagnostics.py
spikes/qwen_tts/requirements.txt
spikes/qwen_tts/runtime.py
spikes/qwen_tts/tests/conftest.py
spikes/qwen_tts/tests/test_validation.py
spikes/qwen_tts/output/.gitkeep
```

Model weights, the synthetic reference, generated WAVs and metrics JSON
files are git-ignored and were NOT committed. No file outside
`spikes/qwen_tts/` was modified.
