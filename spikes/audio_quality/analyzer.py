"""Deterministic audio quality analyzer for voice clone reference recordings.

The analyzer reads a WAV file with ``soundfile`` (no custom decoder), mixes
multi-channel data down to mono for the quality metrics, and computes every
value from the actual samples.  No random values, no hardcoded scores.

Algorithm overview (see also REPORT.md):

* **Mono mixdown**: ``mean(axis=1)`` over channels.  The original file is
  never modified; technical metadata of the original is preserved.
* **dBFS**: ``20 * log10(value / 1.0)``.  Full-scale sine corresponds to
  -3.01 dBFS RMS, full-scale square wave to 0 dBFS RMS.
* **Silence detection**: frame the mono signal (default 20 ms frame,
  10 ms hop).  A frame is "silent" if its RMS dBFS is below
  ``silence_threshold_dbfs`` (default -45 dBFS).  Empty (all-zero) frames
  are also silent.  ``leading_silence`` / ``trailing_silence`` are the
  contiguous silent prefix/suffix converted to milliseconds.
* **Noise floor**: take the RMS dBFS of all *non-empty* frames, then take
  the median of the leisesten ``noise_floor_percentile`` % (default 10 %).
  Non-empty means RMS > 0 (avoids -inf dominating the estimate).
* **SNR**: ``signal_rms_dbfs - noise_floor_dbfs`` where signal RMS is the
  RMS of all active (non-silent) frames.  Returns ``None`` if there are
  no active frames or no non-empty frames.
* **Dropouts**: scan the *active* region (between first and last active
  frame).  A run of consecutive samples with ``|x| <= dropout_sample_abs``
  that lasts at least ``dropout_min_duration_ms`` is counted as one
  dropout.  Known false positives: legitimate pauses inside the active
  region that fall below the threshold; breath intakes with very low
  energy.  This detector is intentionally conservative.
* **Clipping**: any sample with ``|x| >= clipping_magnitude`` (default
  0.999).  Reported as count and ratio over all mono samples.
* **Integrity**: ``has_nan`` / ``has_infinity`` flagged from the raw
  samples.  When detected, the file is rejected and downstream metrics
  are computed on the finite subset only (so the JSON stays valid).

All "not measurable" values (silent file, no active frames, ...) are
serialized as ``null`` instead of ``-inf`` / ``Infinity`` so the JSON
output stays valid per RFC 8259.
"""

from __future__ import annotations

import math
import os
from dataclasses import replace
from typing import Optional

import numpy as np
import soundfile as sf

from models import (
    AnalysisResult,
    DropoutMetrics,
    IntegrityMetrics,
    LevelMetrics,
    NoiseMetrics,
    Quality,
    QualityConfig,
    SilenceMetrics,
    TechnicalMetadata,
    VoiceCloneReference,
)


class AnalysisError(Exception):
    """Raised when the file cannot be read at all."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dbfs(value: float) -> Optional[float]:
    """Convert a linear amplitude value to dBFS.

    Returns ``None`` for non-positive input so the JSON stays valid
    (no ``-Infinity``).
    """

    if value is None or value <= 0.0 or not math.isfinite(value):
        return None
    return 20.0 * math.log10(value)


def _rms_dbfs(samples: np.ndarray) -> Optional[float]:
    if samples.size == 0:
        return None
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return _dbfs(rms)


def _mix_to_mono(data: np.ndarray) -> np.ndarray:
    """Mix multi-channel audio to mono without modifying the original."""

    if data.ndim == 1:
        return data.astype(np.float64, copy=True)
    # mean over channels, preserve dtype semantics as float64
    return data.astype(np.float64).mean(axis=1)


# ---------------------------------------------------------------------------
# core analysis
# ---------------------------------------------------------------------------

class AudioQualityAnalyzer:
    """Deterministic analyzer.  Pure function-style: no global state."""

    def __init__(self, config: Optional[QualityConfig] = None) -> None:
        self.config = config or QualityConfig()

    # -- public ------------------------------------------------------------

    def analyze(self, path: str) -> AnalysisResult:
        if not os.path.isfile(path):
            raise AnalysisError(f"file not found: {path}")

        try:
            data, sample_rate = sf.read(path, always_2d=False, dtype="float64")
        except Exception as exc:  # noqa: BLE001 - we want to surface any read error
            raise AnalysisError(f"failed to read {path}: {exc}") from exc

        info = sf.info(path)
        technical = TechnicalMetadata(
            path=os.path.abspath(path),
            sample_rate=int(sample_rate),
            channels=int(info.channels),
            frame_count=int(info.frames),
            duration_seconds=float(info.frames / sample_rate) if sample_rate else 0.0,
            subtype=info.subtype,
            format=info.format,
        )

        integrity = self._integrity(data)

        # If the file contains NaN/Inf we still try to compute metrics on the
        # finite subset, but the file will be REJECT regardless.
        finite_data = data
        if integrity.has_nan or integrity.has_infinity:
            finite_mask = np.isfinite(data)
            if not finite_mask.all():
                finite_data = data[finite_mask] if data.ndim == 1 else data[np.all(finite_mask, axis=1)]

        mono = _mix_to_mono(finite_data) if finite_data.size else np.zeros(0, dtype=np.float64)

        levels = self._levels(mono)
        silence = self._silence(mono, sample_rate)
        noise = self._noise(mono, sample_rate, silence)
        dropouts = self._dropouts(mono, sample_rate, silence)

        quality, reasons, warnings = self._classify(
            technical, levels, silence, noise, dropouts, integrity
        )
        vcr = self._voice_clone_reference(
            quality, technical, levels, silence, noise, dropouts, integrity, reasons, warnings
        )

        return AnalysisResult(
            technical=technical,
            levels=levels,
            silence=silence,
            noise=noise,
            dropouts=dropouts,
            integrity=integrity,
            quality=quality,
            reasons=reasons,
            warnings=warnings,
            voice_clone_reference=vcr,
            config=self.config,
        )

    # -- components --------------------------------------------------------

    def _integrity(self, data: np.ndarray) -> IntegrityMetrics:
        if data.size == 0:
            return IntegrityMetrics(has_nan=False, has_infinity=False)
        return IntegrityMetrics(
            has_nan=bool(np.isnan(data).any()),
            has_infinity=bool(np.isinf(data).any()),
        )

    def _levels(self, mono: np.ndarray) -> LevelMetrics:
        cfg = self.config
        if mono.size == 0:
            return LevelMetrics(
                peak_dbfs=None,
                rms_dbfs=None,
                dc_offset=0.0,
                clipping_sample_count=0,
                clipping_sample_ratio=0.0,
            )

        peak = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(mono ** 2)))
        dc = float(np.mean(mono))
        clipping_mask = np.abs(mono) >= cfg.clipping_magnitude
        clip_count = int(clipping_mask.sum())
        clip_ratio = float(clip_count / mono.size)

        return LevelMetrics(
            peak_dbfs=_dbfs(peak),
            rms_dbfs=_dbfs(rms),
            dc_offset=dc,
            clipping_sample_count=clip_count,
            clipping_sample_ratio=clip_ratio,
        )

    def _frame_indices(self, n: int, sample_rate: int) -> tuple[int, int]:
        cfg = self.config
        frame_len = max(1, int(round(cfg.frame_ms / 1000.0 * sample_rate)))
        hop_len = max(1, int(round(cfg.hop_ms / 1000.0 * sample_rate)))
        if n < frame_len:
            n_frames = 1 if n > 0 else 0
        else:
            n_frames = 1 + (n - frame_len) // hop_len
        return frame_len, n_frames

    def _silence(self, mono: np.ndarray, sample_rate: int) -> SilenceMetrics:
        cfg = self.config
        n = mono.size
        frame_len, n_frames = self._frame_indices(n, sample_rate)
        hop_len = max(1, int(round(cfg.hop_ms / 1000.0 * sample_rate)))

        if n_frames == 0 or n == 0:
            return SilenceMetrics(
                leading_silence_ms=0.0,
                trailing_silence_ms=0.0,
                total_silence_ratio=1.0 if n == 0 else 0.0,
                voice_ratio=0.0,
                frame_count_total=0,
                frame_count_silent=0,
                frame_count_active=0,
            )

        thr_lin = 10.0 ** (cfg.silence_threshold_dbfs / 20.0)
        is_silent = np.zeros(n_frames, dtype=bool)
        for i in range(n_frames):
            start = i * hop_len
            stop = min(start + frame_len, n)
            frame = mono[start:stop]
            if frame.size == 0:
                is_silent[i] = True
                continue
            rms = float(np.sqrt(np.mean(frame ** 2)))
            # treat all-zero frames as silent (rms == 0 < thr_lin)
            is_silent[i] = rms <= thr_lin

        silent_count = int(is_silent.sum())
        active_count = n_frames - silent_count

        # leading silence = contiguous silent prefix
        leading = 0
        while leading < n_frames and is_silent[leading]:
            leading += 1
        trailing = 0
        while trailing < n_frames and is_silent[n_frames - 1 - trailing]:
            trailing += 1

        # Convert frame counts to milliseconds based on hop.  Leading silence
        # covers `leading` hops; the very first frame adds frame_len, but for
        # consistency we use hop-based timing (matches the framing grid).
        leading_ms = leading * hop_len / sample_rate * 1000.0 if leading < n_frames else n / sample_rate * 1000.0
        trailing_ms = trailing * hop_len / sample_rate * 1000.0 if trailing < n_frames else n / sample_rate * 1000.0

        total_ratio = silent_count / n_frames if n_frames else 0.0

        return SilenceMetrics(
            leading_silence_ms=float(leading_ms),
            trailing_silence_ms=float(trailing_ms),
            total_silence_ratio=float(total_ratio),
            voice_ratio=float(1.0 - total_ratio),
            frame_count_total=int(n_frames),
            frame_count_silent=silent_count,
            frame_count_active=active_count,
        )

    def _noise(self, mono: np.ndarray, sample_rate: int, silence: SilenceMetrics) -> NoiseMetrics:
        cfg = self.config
        if mono.size == 0 or silence.frame_count_total == 0:
            return NoiseMetrics(
                estimated_noise_floor_dbfs=None,
                estimated_snr_db=None,
                active_frames_used=0,
            )

        frame_len, n_frames = self._frame_indices(mono.size, sample_rate)
        hop_len = max(1, int(round(cfg.hop_ms / 1000.0 * sample_rate)))

        rms_dbfs_frames = np.empty(n_frames, dtype=np.float64)
        for i in range(n_frames):
            start = i * hop_len
            stop = min(start + frame_len, mono.size)
            frame = mono[start:stop]
            if frame.size == 0:
                rms_dbfs_frames[i] = np.nan
                continue
            rms = float(np.sqrt(np.mean(frame ** 2)))
            rms_dbfs_frames[i] = _dbfs(rms) if rms > 0 else np.nan

        # non-empty = frames with measurable RMS (rms > 0)
        finite_mask = ~np.isnan(rms_dbfs_frames)
        finite_frames = rms_dbfs_frames[finite_mask]
        if finite_frames.size == 0:
            return NoiseMetrics(None, None, 0)

        # Noise floor: median of the leisesten X% of non-empty frames
        k = max(1, int(math.ceil(finite_frames.size * cfg.noise_floor_percentile / 100.0)))
        quietest = np.sort(finite_frames)[:k]
        noise_floor_dbfs = float(np.median(quietest))

        # Signal: RMS of active frames (non-silent).  Re-derive silence mask
        # from the silence result by recomputing the threshold comparison.
        thr_lin = 10.0 ** (cfg.silence_threshold_dbfs / 20.0)
        active_rms_lin = []
        for i in range(n_frames):
            start = i * hop_len
            stop = min(start + frame_len, mono.size)
            frame = mono[start:stop]
            if frame.size == 0:
                continue
            rms = float(np.sqrt(np.mean(frame ** 2)))
            if rms > thr_lin:
                active_rms_lin.append(rms)

        if not active_rms_lin:
            return NoiseMetrics(
                estimated_noise_floor_dbfs=noise_floor_dbfs,
                estimated_snr_db=None,
                active_frames_used=0,
            )

        signal_rms = float(np.sqrt(np.mean(np.asarray(active_rms_lin) ** 2)))
        signal_dbfs = _dbfs(signal_rms)
        if signal_dbfs is None:
            snr = None
        else:
            snr = float(signal_dbfs - noise_floor_dbfs)

        return NoiseMetrics(
            estimated_noise_floor_dbfs=noise_floor_dbfs,
            estimated_snr_db=snr,
            active_frames_used=len(active_rms_lin),
        )

    def _dropouts(self, mono: np.ndarray, sample_rate: int, silence: SilenceMetrics) -> DropoutMetrics:
        cfg = self.config
        if mono.size == 0 or silence.frame_count_total == 0:
            return DropoutMetrics(0, 0.0, 0.0)

        # Active region = between first and last active frame.
        frame_len, n_frames = self._frame_indices(mono.size, sample_rate)
        hop_len = max(1, int(round(cfg.hop_ms / 1000.0 * sample_rate)))
        thr_lin = 10.0 ** (cfg.silence_threshold_dbfs / 20.0)

        first_active = None
        last_active = None
        for i in range(n_frames):
            start = i * hop_len
            stop = min(start + frame_len, mono.size)
            frame = mono[start:stop]
            if frame.size == 0:
                continue
            rms = float(np.sqrt(np.mean(frame ** 2)))
            if rms > thr_lin:
                if first_active is None:
                    first_active = i
                last_active = i

        if first_active is None:
            return DropoutMetrics(0, 0.0, 0.0)

        start_sample = first_active * hop_len
        end_sample = min((last_active + 1) * hop_len, mono.size)
        region = mono[start_sample:end_sample]
        if region.size == 0:
            return DropoutMetrics(0, 0.0, 0.0)

        near_zero = np.abs(region) <= cfg.dropout_sample_abs
        min_samples = max(1, int(round(cfg.dropout_min_duration_ms / 1000.0 * sample_rate)))

        count = 0
        longest = 0
        total = 0
        run = 0
        for flag in near_zero:
            if flag:
                run += 1
            else:
                if run >= min_samples:
                    count += 1
                    total += run
                    if run > longest:
                        longest = run
                run = 0
        if run >= min_samples:
            count += 1
            total += run
            if run > longest:
                longest = run

        ms_per_sample = 1000.0 / sample_rate
        return DropoutMetrics(
            dropout_count=count,
            dropout_total_ms=float(total * ms_per_sample),
            longest_dropout_ms=float(longest * ms_per_sample),
        )

    # -- classification ----------------------------------------------------

    def _classify(
        self,
        technical: TechnicalMetadata,
        levels: LevelMetrics,
        silence: SilenceMetrics,
        noise: NoiseMetrics,
        dropouts: DropoutMetrics,
        integrity: IntegrityMetrics,
    ) -> tuple[Quality, list[str], list[str]]:
        cfg = self.config
        reasons: list[str] = []
        warnings: list[str] = []

        # --- REJECT conditions ------------------------------------------------
        if integrity.has_nan:
            reasons.append("File contains NaN samples.")
        if integrity.has_infinity:
            reasons.append("File contains Infinity samples.")
        if technical.frame_count == 0 or technical.duration_seconds <= 0:
            reasons.append("File has no frames.")
        if silence.total_silence_ratio >= cfg.near_silence_ratio:
            reasons.append("Near-complete silence.")
        if levels.clipping_sample_ratio >= cfg.severe_clipping_ratio:
            reasons.append(f"Severe clipping ratio {levels.clipping_sample_ratio:.3e}.")
        if technical.duration_seconds < cfg.absolute_min_seconds:
            reasons.append(
                f"Recording shorter than absolute minimum {cfg.absolute_min_seconds}s."
            )

        if reasons:
            return Quality.REJECT, reasons, warnings

        # --- REVIEW conditions -------------------------------------------------
        if noise.estimated_snr_db is not None and noise.estimated_snr_db < cfg.low_snr_db:
            warnings.append(
                f"Low estimated SNR {noise.estimated_snr_db:.1f} dB "
                f"(threshold {cfg.low_snr_db} dB)."
            )
        if technical.duration_seconds < cfg.reference_min_seconds:
            warnings.append(
                f"Reference shorter than preferred {cfg.reference_min_seconds}s."
            )
        if technical.duration_seconds > cfg.reference_max_seconds:
            warnings.append(
                f"Reference is longer than the preferred "
                f"{cfg.reference_min_seconds}-{cfg.reference_max_seconds} second range."
            )
        if silence.leading_silence_ms > cfg.long_silence_ms:
            warnings.append(
                f"Leading silence {silence.leading_silence_ms:.0f} ms is long."
            )
        if silence.trailing_silence_ms > cfg.long_silence_ms:
            warnings.append(
                f"Trailing silence {silence.trailing_silence_ms:.0f} ms is long."
            )
        if levels.clipping_sample_ratio >= cfg.isolated_clipping_ratio:
            warnings.append(
                f"Isolated clipping detected ({levels.clipping_sample_count} samples)."
            )
        if dropouts.dropout_count > 0:
            warnings.append(
                f"{dropouts.dropout_count} dropout(s) detected inside active region."
            )
        if abs(levels.dc_offset) >= cfg.severe_dc_offset:
            warnings.append(
                f"DC offset {levels.dc_offset:.4f} is high."
            )

        if warnings:
            return Quality.REVIEW, reasons, warnings

        # --- EXCELLENT vs GOOD -------------------------------------------------
        excellent = True
        if levels.clipping_sample_count > 0:
            excellent = False
        if noise.estimated_snr_db is None or noise.estimated_snr_db < cfg.good_snr_db:
            excellent = False
        if not (cfg.reference_min_seconds <= technical.duration_seconds <= cfg.reference_max_seconds):
            excellent = False
        if silence.leading_silence_ms > 100.0 or silence.trailing_silence_ms > 100.0:
            excellent = False
        if dropouts.dropout_count > 0:
            excellent = False
        if abs(levels.dc_offset) >= 0.01:
            excellent = False

        if excellent:
            return Quality.EXCELLENT, reasons, warnings
        return Quality.GOOD, reasons, warnings

    def _voice_clone_reference(
        self,
        quality: Quality,
        technical: TechnicalMetadata,
        levels: LevelMetrics,
        silence: SilenceMetrics,
        noise: NoiseMetrics,
        dropouts: DropoutMetrics,
        integrity: IntegrityMetrics,
        reasons: list[str],
        warnings: list[str],
    ) -> VoiceCloneReference:
        eligible = quality in (Quality.EXCELLENT, Quality.GOOD)
        vcr_reasons: list[str] = []
        vcr_warnings: list[str] = []

        if quality == Quality.REJECT:
            vcr_reasons = list(reasons)
        elif quality == Quality.REVIEW:
            vcr_warnings = list(warnings)

        # Voice-clone-specific framing (does NOT claim anything about
        # pronunciation, speaker identity or transcript).
        if eligible and technical.duration_seconds > self.config.reference_max_seconds:
            vcr_warnings.append(
                "Reference is longer than the preferred 5-12 second range."
            )
        if eligible and technical.duration_seconds < self.config.reference_min_seconds:
            vcr_warnings.append(
                "Reference is shorter than the preferred 5-12 second range."
            )

        return VoiceCloneReference(
            eligible=eligible,
            quality=quality,
            reasons=vcr_reasons,
            warnings=vcr_warnings,
        )
