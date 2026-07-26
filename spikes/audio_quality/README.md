# Audio Quality Spike (Voice Clone Reference)

Deterministic, sample-based quality analysis for WAV recordings that are
later used as voice clone references.  This is a **spike**: no production
integration, no API, no UI.

## Scope

* Works on real `.wav` files via `soundfile` (no custom decoder).
* Computes every metric from the actual samples — no random values, no
  hardcoded scores.
* Multi-channel files are mixed down to mono for the quality metrics; the
  original file is never modified and its technical metadata is preserved.
* All "not measurable" values are serialized as `null` so the JSON output
  is valid per RFC 8259 (no `Infinity` / `-Infinity`).

## Layout

```
spikes/audio_quality/
├── analyze.py        # CLI entry point
├── analyzer.py       # core analysis
├── models.py         # dataclasses, configuration, Quality enum
├── requirements.txt
├── README.md
├── REPORT.md         # results of the spike
├── tests/            # pytest suite, generates fixtures in-memory
└── fixtures/.gitkeep
```

## Usage

```powershell
python spikes/audio_quality/analyze.py recordings/example.wav
python spikes/audio_quality/analyze.py input.wav --output result.json
python spikes/audio_quality/analyze.py input.wav --silence-threshold-dbfs -42
python spikes/audio_quality/analyze.py input.wav --pretty
```

Output:

* `<input>.analysis.json` (or the path given via `--output`)
* a human-readable console summary

### Exit codes

| Code | Meaning                                       |
|------|-----------------------------------------------|
| 0    | Analysis successful, quality GOOD or EXCELLENT |
| 1    | Analysis successful, quality REVIEW           |
| 2    | Analysis successful, quality REJECT           |
| 3    | Technical error (unreadable file, etc.)       |

## Metrics

```
duration_seconds
sample_rate
channels
frame_count
peak_dbfs
rms_dbfs
dc_offset
clipping_sample_count
clipping_sample_ratio
leading_silence_ms
trailing_silence_ms
total_silence_ratio
estimated_noise_floor_dbfs
estimated_snr_db
dropout_count
has_nan
has_infinity
```

## Algorithms (short version, see REPORT.md for full details)

* **Mono mixdown**: `mean(axis=1)` over channels (float64).
* **dBFS**: `20 * log10(value / 1.0)`; non-positive values become `null`.
* **Silence**: 20 ms frame, 10 ms hop, frame RMS <= -45 dBFS → silent.
  Empty (all-zero) frames are silent.  Leading/trailing silence are the
  contiguous silent prefix/suffix in ms.
* **Noise floor**: median of the RMS dBFS of the leisesten 10 % of
  *non-empty* frames (frames with RMS > 0).
* **SNR**: `signal_rms_dbfs - noise_floor_dbfs`, where signal RMS is the
  RMS of all active (non-silent) frames.  `null` if no active frames.
* **Dropouts**: runs of `|x| <= 1e-4` inside the active region lasting at
  least 20 ms.  Known false positives: legitimate pauses / breath intakes
  inside the active region.
* **Clipping**: `|x| >= 0.999` (configurable).

## Quality classification

`REJECT` → `REVIEW` → `GOOD` → `EXCELLENT`.  All thresholds live in
`QualityConfig` (`models.py`) and can be tuned in a single place.

## Voice clone recommendation

The JSON contains a `voice_clone_reference` block:

```json
{
  "voice_clone_reference": {
    "eligible": true,
    "quality": "GOOD",
    "reasons": [],
    "warnings": [
      "Reference is longer than the preferred 5-12 second range."
    ]
  }
}
```

This recommendation is **purely technical**.  It does not assess
pronunciation, speaker identity, or whether the transcript matches.

## Tests

```powershell
cd spikes/audio_quality
python -m pytest -q
```

Fixtures are generated in-memory inside `tests/` (via `soundfile.write`
to a `tmp_path`).  No large audio files are committed.

## Limits

* No real VAD — silence detection is energy-based and will misclassify
  very quiet speech as silence.
* Noise floor is a heuristic; on very short or very clean recordings the
  estimate can be unreliable.
* Dropouts are detected by amplitude only; short zero-runs at frame
  boundaries may be missed.
* No GPU required.  Silero VAD may be added later as an optional
  refinement, but the base analysis must run on CPU.
