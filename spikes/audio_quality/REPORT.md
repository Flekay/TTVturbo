# Audio Quality Spike — Report

Spike for deterministic, sample-based quality analysis of WAV recordings
used as voice clone references.

**Status:** `IMPLEMENTED AND TESTED`

The analyzer, CLI, and pytest suite are implemented and all 29 tests pass.
Real browser recordings from `recordings/` were analyzed; the analysis is
reproducible from the committed source code.  No production integration
was performed (per scope).

## Scope reminder

This spike lives entirely under `spikes/audio_quality/`.  No other file in
the repository was modified.  No API, no React UI, no database, no Qwen
integration.

## Algorithms

### Mono mixdown

Multi-channel WAV data is read with `soundfile.read(..., dtype="float64")`.
For quality metrics the channels are averaged:

```
mono = mean(data, axis=1)
```

The original file is never modified.  Technical metadata of the original
(`sample_rate`, `channels`, `frame_count`, `duration_seconds`, `subtype`,
`format`) is preserved in `AnalysisResult.technical`.

### dBFS

```
dBFS(x) = 20 * log10(|x| / 1.0)
```

For RMS: `RMS = sqrt(mean(samples^2))`, then `20 * log10(RMS)`.
Non-positive values (silent file, zero RMS) are serialized as `null` so
the JSON stays valid per RFC 8259 (no `Infinity` / `-Infinity`).

### Clipping

A sample is counted as clipped when `|x| >= clipping_magnitude` (default
`0.999`, configurable via `QualityConfig` and `--clipping-magnitude`).
Both absolute count and ratio over all mono samples are reported.

### Silence detection

Frame-based energy analysis:

* Frame length: 20 ms (configurable via `--frame-ms`)
* Hop: 10 ms (configurable via `--hop-ms`)
* Silence threshold: -45 dBFS RMS (configurable via
  `--silence-threshold-dbfs`)

A frame is *silent* when its RMS dBFS is at or below the threshold.
All-zero frames (RMS = 0) are silent by definition.

Derived values:

* `leading_silence_ms` — contiguous silent prefix, converted via hop grid
* `trailing_silence_ms` — contiguous silent suffix
* `total_silence_ratio` — `silent_frames / total_frames`
* `voice_ratio` — `1 - total_silence_ratio` (an approximation; this is
  **not** a VAD and must not be described as speech recognition)

### Noise floor and SNR

```
Noise floor:
  Take the RMS dBFS of every non-empty frame (RMS > 0).
  Sort ascending.
  Take the median of the leisesten 10 % (configurable via
  --noise-floor-percentile).

Signal:
  RMS of all active (non-silent) frames, converted to dBFS.

SNR:
  signal_rms_dbfs - noise_floor_dbfs
```

If there are no active frames or no non-empty frames, both
`estimated_noise_floor_dbfs` and `estimated_snr_db` are `null`.

**Known limitation:** when a recording has no real silent gaps (continuous
tone / continuous speech), the "leisesten 10 %" of non-empty frames are
still loud, so the noise floor is over-estimated and the SNR collapses
toward 0 dB.  This is documented below and visible in the fixture
results.  A real VAD (e.g. Silero) would be needed to separate speech
pauses from background noise reliably.

### Dropouts

A *dropout* is a run of consecutive samples with `|x| <= dropout_sample_abs`
(default `1e-4`) inside the *active region* (between the first and last
active frame) lasting at least `dropout_min_duration_ms` (default 20 ms).

Reported: `dropout_count`, `dropout_total_ms`, `longest_dropout_ms`.

**Known false positives:**

* Legitimate pauses / breath intakes inside the active region that fall
  below the amplitude threshold.
* Very low-energy fricatives in quiet speech.

The detector is intentionally conservative (20 ms minimum, very low
threshold) to limit false positives.

### Integrity

`has_nan` and `has_infinity` are flagged from the raw samples.  When
detected, the file is `REJECT` and downstream metrics are computed on the
finite subset only so the JSON stays valid.

## Quality classification

All thresholds live in `QualityConfig` (`models.py`).

| Class      | Trigger (summary)                                                       |
|------------|-------------------------------------------------------------------------|
| `REJECT`   | unreadable, no frames, NaN/Inf, near-complete silence (>= 99 %), severe clipping (ratio >= 1e-3), duration < 1 s |
| `REVIEW`   | SNR < 15 dB, duration outside 5–12 s, leading/trailing silence > 500 ms, isolated clipping (ratio >= 1e-5), any dropout, DC offset >= 0.05 |
| `GOOD`     | technically clean, no critical issues, minor warnings possible          |
| `EXCELLENT`| no clipping, SNR >= 25 dB, duration in 5–12 s, leading/trailing silence <= 100 ms, no dropouts, DC offset < 0.01 |

### Voice clone recommendation

A `voice_clone_reference` block is emitted with `eligible`, `quality`,
`reasons`, and `warnings`.  This recommendation is **purely technical**.
It does not assess pronunciation, speaker identity, or whether the
transcript matches the recording.

## Measured values

### 1. Real browser recording: `recordings/ab05fc44cbe642088dbf96a3f69706d3.wav`

A real browser recording from the repository.  Full JSON saved as
`fixtures/real_ab05fc44.analysis.json`.

```
sample_rate:              44100 Hz
channels:                 1
duration_seconds:         4.494
peak_dbfs:                0.0
rms_dbfs:                 -21.08
dc_offset:                0.000003
clipping_samples:         24 (ratio 1.211e-04)
leading_silence_ms:       790
trailing_silence_ms:      860
total_silence_ratio:      0.533
voice_ratio:              0.467
estimated_noise_floor:    -101.38 dBFS
estimated_snr_db:         83.62
dropout_count:            1 (longest 37.1 ms)
has_nan:                  False
has_infinity:             False
quality:                  REVIEW
```

Warnings: shorter than preferred 5 s, long leading/trailing silence,
isolated clipping (24 samples at full scale), 1 dropout inside the active
region.

This recording shows the analyzer working well on real data: the silent
gaps give a reliable noise floor (-101 dBFS, essentially digital silence),
so the SNR estimate (83 dB) is meaningful.  The 24 full-scale samples are
flagged as isolated clipping — likely digital clipping in the browser
capture path.

### 2. Real browser recording: `recordings/02b39fb41c0242b7877607034b265cd9.wav`

```
sample_rate:              44100 Hz
channels:                 1
duration_seconds:         2.000
peak_dbfs:                -17.86
rms_dbfs:                 -21.08
dc_offset:                -0.000021
clipping_samples:         0
total_silence_ratio:      0.000
voice_ratio:              1.000
estimated_noise_floor:    -21.15 dBFS
estimated_snr_db:         0.07
dropout_count:            0
quality:                  REVIEW
```

Warnings: low SNR 0.1 dB, shorter than preferred 5 s.

This recording has **no silent frames at all** (continuous signal), so the
noise floor heuristic collapses: the leisesten 10 % of non-empty frames
are at -21 dBFS, essentially the same level as the signal.  The SNR
estimate is therefore not meaningful.  This is the principal known
limitation of the energy-based approach (see below).

### 3. Real browser recording: `recordings/8636082853ab481cbc8d3ca0da49decd.wav`

```
duration_seconds:         6.634
peak_dbfs:                -36.85
rms_dbfs:                 -66.63
total_silence_ratio:      1.000
voice_ratio:              0.000
estimated_snr_db:         null
quality:                  REJECT
```

Reason: near-complete silence.  The recording is effectively empty (peak
-36.85 dBFS, all 662 frames below the -45 dBFS threshold).  Correctly
rejected.

### 4. Controlled clean fixture: `fixtures/clean_voice_like.wav`

Synthetic 8.35 s signal: fundamental + harmonics with a 2.5 Hz amplitude
envelope, 200 ms leading + 150 ms trailing silence.

```
duration_seconds:         8.350
peak_dbfs:                -11.36
rms_dbfs:                 -19.44
dc_offset:                0.000000
clipping_samples:         0
leading_silence_ms:       190
trailing_silence_ms:      140
total_silence_ratio:      0.040
estimated_noise_floor:    -29.51 dBFS
estimated_snr_db:         10.25
dropout_count:            0
quality:                  REVIEW
```

Warning: low SNR 10.3 dB.  The synthetic envelope never goes fully silent
inside the "active" region, so the noise floor is over-estimated.  This
confirms the SNR limitation on continuous signals.

### 5. Intentionally faulty fixture: `fixtures/clipped_fixture.wav`

6 s of an overdriven two-tone signal hard-clipped at +/-1.0.

```
duration_seconds:         6.000
peak_dbfs:                0.0
rms_dbfs:                 -2.58
clipping_samples:         112320 (ratio 4.245e-01)
total_silence_ratio:      0.000
estimated_snr_db:         0.20
quality:                  REJECT
```

Reason: severe clipping ratio 4.245e-01.  Correctly rejected.

### 6. Low-SNR fixture: `fixtures/noisy_low_snr.wav`

5 s of a 220 Hz tone (amplitude 0.2) plus broadband noise (amplitude 0.05).

```
duration_seconds:         5.000
peak_dbfs:                -7.88
rms_dbfs:                 -16.48
clipping_samples:         0
total_silence_ratio:      0.000
estimated_snr_db:         0.19
quality:                  REVIEW
```

Warning: low SNR 0.2 dB.  Again, no silent frames -> noise floor
heuristic unreliable on a continuous signal.

## Recognized warnings (summary across the corpus)

* Real recordings with silent gaps -> reliable SNR (83 dB observed).
* Real recordings without silent gaps -> SNR collapses toward 0 dB; the
  warning is technically correct ("low SNR") but the cause is the
  estimator, not the recording.
* Several browser recordings are effectively silent (REJECT).
* One real recording has 24 full-scale samples (isolated clipping) and a
  37 ms dropout inside the active region.
* Several recordings are shorter than the preferred 5 s voice clone
  reference length.

## Known limits

1. **No real VAD.**  Silence detection is energy-based.  Very quiet
   speech is misclassified as silence; the `voice_ratio` is an
   approximation, not a speech detector.
2. **SNR estimator fails on continuous signals.**  Without real silent
   gaps, the "leisesten 10 % of non-empty frames" is still loud and the
   SNR collapses toward 0 dB.  A real VAD (Silero, WebRTC VAD) would be
   needed to separate speech pauses from background noise.  Silero VAD
   may be added later as an optional refinement; the base analysis runs
   on CPU only.
3. **Dropouts are amplitude-only.**  Short zero-runs at frame boundaries
   may be missed.  Legitimate pauses / breath intakes inside the active
   region can be false positives.
4. **Noise floor is a heuristic.**  On very short or very clean
   recordings the estimate can be unreliable; on fully continuous
   recordings it is not meaningful.
5. **Clipping detection is amplitude-only.**  Soft clipping (e.g. a
   limiter) below `0.999` is not detected.
6. **No codec / container analysis.**  The analyzer trusts `soundfile`'s
   decoding.
7. **Voice clone recommendation is technical only.**  It does not assess
   pronunciation, speaker identity, or transcript match.

## Recommended thresholds for TTVturbo

Based on the corpus analyzed here, the following starting thresholds are
recommended for the TTVturbo voice clone reference pipeline.  They are
the current defaults in `QualityConfig` and should be re-validated once
more real recordings are available.

```
# silence / framing
frame_ms:                  20.0
hop_ms:                    10.0
silence_threshold_dbfs:    -45.0

# clipping
clipping_magnitude:        0.999

# noise floor / SNR
noise_floor_percentile:    10.0

# dropouts
dropout_min_duration_ms:   20.0
dropout_sample_abs:        1e-4

# reference duration (voice clone)
reference_min_seconds:     5.0
reference_max_seconds:     12.0
absolute_min_seconds:      1.0

# classification
near_silence_ratio:        0.99
severe_clipping_ratio:     1e-3
isolated_clipping_ratio:   1e-5
low_snr_db:                15.0
good_snr_db:               25.0
long_silence_ms:           500.0
severe_dc_offset:          0.05
```

**Action items for production hardening (out of scope for this spike):**

* Add Silero VAD as an optional refinement for `voice_ratio` and for a
  more reliable noise floor / SNR estimate on continuous speech.
* Re-validate `low_snr_db` and `good_snr_db` once VAD-based noise floor
  estimates are available; the current 15 / 25 dB thresholds are
  reasonable for VAD-based estimates but not for the pure energy-based
  estimator on continuous signals.
* Collect a labeled set of known-good and known-bad references and tune
  `isolated_clipping_ratio` and `dropout_min_duration_ms` against it.
* Consider a soft-clipping detector (slew-rate / THD-based) for the
  review path.

## How to reproduce

```powershell
# from the repository root
pip install -r spikes/audio_quality/requirements.txt
python -m pytest spikes/audio_quality -q

# generate the fixtures used in this report
python spikes/audio_quality/make_report_fixtures.py

# analyze a real recording
python spikes/audio_quality/analyze.py recordings/ab05fc44cbe642088dbf96a3f69706d3.wav --pretty

# analyze a fixture
python spikes/audio_quality/analyze.py spikes/audio_quality/fixtures/clean_voice_like.wav --pretty
```

## Status

```
IMPLEMENTED AND TESTED
```

The analyzer, CLI, tests, and this report are complete.  Real browser
recordings from `recordings/` were analyzed successfully.  No production
integration was performed, per the spike scope.
