"""Deterministic audio quality analyzer for voice-clone reference recordings.

Adapted from ``spikes/audio_quality`` (IMPLEMENTED AND TESTED). The spike
remains untouched; this is the production copy. The algorithm is identical
to the verified spike: mono mixdown, dBFS, frame-based silence detection,
noise floor / SNR, dropouts, clipping, integrity, and a conservative
REJECT / REVIEW / GOOD / EXCELLENT classification with a
``voice_clone_reference`` recommendation block.

No value is ever fabricated. "Not measurable" values serialize as ``None``
so the JSON stays RFC 8259 compliant (no ``Infinity`` / ``-Infinity``).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np
import soundfile as sf


class Quality(str, Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


@dataclass
class QualityConfig:
    """Tunable thresholds for the analyzer.

    Defaults are intentionally conservative for voice-clone references and
    are NOT derived from a trained model.
    """

    frame_ms: float = 20.0
    hop_ms: float = 10.0
    silence_threshold_dbfs: float = -45.0
    clipping_magnitude: float = 0.999
    noise_floor_percentile: float = 10.0
    dropout_min_duration_ms: float = 20.0
    dropout_sample_abs: float = 1e-4
    reference_min_seconds: float = 5.0
    reference_max_seconds: float = 12.0
    absolute_min_seconds: float = 1.0
    near_silence_ratio: float = 0.99
    severe_clipping_ratio: float = 1e-3
    isolated_clipping_ratio: float = 1e-5
    low_snr_db: float = 15.0
    good_snr_db: float = 25.0
    long_silence_ms: float = 500.0
    severe_dc_offset: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_ms": self.frame_ms,
            "hop_ms": self.hop_ms,
            "silence_threshold_dbfs": self.silence_threshold_dbfs,
            "clipping_magnitude": self.clipping_magnitude,
            "noise_floor_percentile": self.noise_floor_percentile,
            "dropout_min_duration_ms": self.dropout_min_duration_ms,
            "dropout_sample_abs": self.dropout_sample_abs,
            "reference_min_seconds": self.reference_min_seconds,
            "reference_max_seconds": self.reference_max_seconds,
            "absolute_min_seconds": self.absolute_min_seconds,
            "near_silence_ratio": self.near_silence_ratio,
            "severe_clipping_ratio": self.severe_clipping_ratio,
            "isolated_clipping_ratio": self.isolated_clipping_ratio,
            "low_snr_db": self.low_snr_db,
            "good_snr_db": self.good_snr_db,
            "long_silence_ms": self.long_silence_ms,
            "severe_dc_offset": self.severe_dc_offset,
        }


@dataclass
class TechnicalMetadata:
    sample_rate: int
    channels: int
    frame_count: int
    duration_seconds: float
    subtype: Optional[str] = None
    format: Optional[str] = None


@dataclass
class LevelMetrics:
    peak_dbfs: Optional[float]
    rms_dbfs: Optional[float]
    dc_offset: float
    clipping_sample_count: int
    clipping_sample_ratio: float


@dataclass
class SilenceMetrics:
    leading_silence_ms: float
    trailing_silence_ms: float
    total_silence_ratio: float
    voice_ratio: float
    frame_count_total: int
    frame_count_silent: int
    frame_count_active: int


@dataclass
class NoiseMetrics:
    estimated_noise_floor_dbfs: Optional[float]
    estimated_snr_db: Optional[float]
    active_frames_used: int


@dataclass
class DropoutMetrics:
    dropout_count: int
    dropout_total_ms: float
    longest_dropout_ms: float


@dataclass
class IntegrityMetrics:
    has_nan: bool
    has_infinity: bool


@dataclass
class VoiceCloneReference:
    eligible: bool
    quality: Quality
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    technical: TechnicalMetadata
    levels: LevelMetrics
    silence: SilenceMetrics
    noise: NoiseMetrics
    dropouts: DropoutMetrics
    integrity: IntegrityMetrics
    quality: Quality
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    voice_clone_reference: VoiceCloneReference = field(
        default_factory=lambda: VoiceCloneReference(False, Quality.REJECT)
    )
    config: QualityConfig = field(default_factory=QualityConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "technical": {
                "sample_rate": self.technical.sample_rate,
                "channels": self.technical.channels,
                "frame_count": self.technical.frame_count,
                "duration_seconds": self.technical.duration_seconds,
                "subtype": self.technical.subtype,
                "format": self.technical.format,
            },
            "levels": {
                "peak_dbfs": self.levels.peak_dbfs,
                "rms_dbfs": self.levels.rms_dbfs,
                "dc_offset": self.levels.dc_offset,
                "clipping_sample_count": self.levels.clipping_sample_count,
                "clipping_sample_ratio": self.levels.clipping_sample_ratio,
            },
            "silence": {
                "leading_silence_ms": self.silence.leading_silence_ms,
                "trailing_silence_ms": self.silence.trailing_silence_ms,
                "total_silence_ratio": self.silence.total_silence_ratio,
                "voice_ratio": self.silence.voice_ratio,
                "frame_count_total": self.silence.frame_count_total,
                "frame_count_silent": self.silence.frame_count_silent,
                "frame_count_active": self.silence.frame_count_active,
            },
            "noise": {
                "estimated_noise_floor_dbfs": self.noise.estimated_noise_floor_dbfs,
                "estimated_snr_db": self.noise.estimated_snr_db,
                "active_frames_used": self.noise.active_frames_used,
            },
            "dropouts": {
                "dropout_count": self.dropouts.dropout_count,
                "dropout_total_ms": self.dropouts.dropout_total_ms,
                "longest_dropout_ms": self.dropouts.longest_dropout_ms,
            },
            "integrity": {
                "has_nan": self.integrity.has_nan,
                "has_infinity": self.integrity.has_infinity,
            },
            "quality": self.quality.value,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "voice_clone_reference": {
                "eligible": self.voice_clone_reference.eligible,
                "quality": self.voice_clone_reference.quality.value,
                "reasons": self.voice_clone_reference.reasons,
                "warnings": self.voice_clone_reference.warnings,
            },
            "config": self.config.to_dict(),
        }


class AnalysisError(Exception):
    """Raised when the file cannot be read at all."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dbfs(value: float) -> Optional[float]:
    if value is None or value <= 0.0 or not math.isfinite(value):
        return None
    return 20.0 * math.log10(value)


def _mix_to_mono(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        return data.astype(np.float64, copy=True)
    return data.astype(np.float64).mean(axis=1)


class AudioQualityAnalyzer:
    """Deterministic analyzer. Pure function-style: no global state."""

    def __init__(self, config: Optional[QualityConfig] = None) -> None:
        self.config = config or QualityConfig()

    def analyze(self, path: str) -> AnalysisResult:
        if not os.path.isfile(path):
            raise AnalysisError(f"file not found: {path}")

        try:
            data, sample_rate = sf.read(path, always_2d=False, dtype="float64")
        except Exception as exc:
            raise AnalysisError(f"failed to read {path}: {exc}") from exc

        info = sf.info(path)
        technical = TechnicalMetadata(
            sample_rate=int(sample_rate),
            channels=int(info.channels),
            frame_count=int(info.frames),
            duration_seconds=float(info.frames / sample_rate) if sample_rate else 0.0,
            subtype=info.subtype,
            format=info.format,
        )

        integrity = self._integrity(data)

        finite_data = data
        if integrity.has_nan or integrity.has_infinity:
            finite_mask = np.isfinite(data)
            if not finite_mask.all():
                finite_data = (
                    data[finite_mask]
                    if data.ndim == 1
                    else data[np.all(finite_mask, axis=1)]
                )

        mono = (
            _mix_to_mono(finite_data)
            if finite_data.size
            else np.zeros(0, dtype=np.float64)
        )

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
            is_silent[i] = rms <= thr_lin

        silent_count = int(is_silent.sum())
        active_count = n_frames - silent_count

        leading = 0
        while leading < n_frames and is_silent[leading]:
            leading += 1
        trailing = 0
        while trailing < n_frames and is_silent[n_frames - 1 - trailing]:
            trailing += 1

        leading_ms = (
            leading * hop_len / sample_rate * 1000.0
            if leading < n_frames
            else n / sample_rate * 1000.0
        )
        trailing_ms = (
            trailing * hop_len / sample_rate * 1000.0
            if trailing < n_frames
            else n / sample_rate * 1000.0
        )
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
            return NoiseMetrics(None, None, 0)

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

        finite_mask = ~np.isnan(rms_dbfs_frames)
        finite_frames = rms_dbfs_frames[finite_mask]
        if finite_frames.size == 0:
            return NoiseMetrics(None, None, 0)

        k = max(1, int(math.ceil(finite_frames.size * cfg.noise_floor_percentile / 100.0)))
        quietest = np.sort(finite_frames)[:k]
        noise_floor_dbfs = float(np.median(quietest))

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
            return NoiseMetrics(noise_floor_dbfs, None, 0)

        signal_rms = float(np.sqrt(np.mean(np.asarray(active_rms_lin) ** 2)))
        signal_dbfs = _dbfs(signal_rms)
        snr = None if signal_dbfs is None else float(signal_dbfs - noise_floor_dbfs)

        return NoiseMetrics(noise_floor_dbfs, snr, len(active_rms_lin))

    def _dropouts(self, mono: np.ndarray, sample_rate: int, silence: SilenceMetrics) -> DropoutMetrics:
        cfg = self.config
        if mono.size == 0 or silence.frame_count_total == 0:
            return DropoutMetrics(0, 0.0, 0.0)

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
            warnings.append(f"Leading silence {silence.leading_silence_ms:.0f} ms is long.")
        if silence.trailing_silence_ms > cfg.long_silence_ms:
            warnings.append(f"Trailing silence {silence.trailing_silence_ms:.0f} ms is long.")
        if levels.clipping_sample_ratio >= cfg.isolated_clipping_ratio:
            warnings.append(
                f"Isolated clipping detected ({levels.clipping_sample_count} samples)."
            )
        if dropouts.dropout_count > 0:
            warnings.append(
                f"{dropouts.dropout_count} dropout(s) detected inside active region."
            )
        if abs(levels.dc_offset) >= cfg.severe_dc_offset:
            warnings.append(f"DC offset {levels.dc_offset:.4f} is high.")

        if warnings:
            return Quality.REVIEW, reasons, warnings

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

        if eligible and technical.duration_seconds > self.config.reference_max_seconds:
            vcr_warnings.append("Reference is longer than the preferred 5-12 second range.")
        if eligible and technical.duration_seconds < self.config.reference_min_seconds:
            vcr_warnings.append("Reference is shorter than the preferred 5-12 second range.")

        return VoiceCloneReference(
            eligible=eligible,
            quality=quality,
            reasons=vcr_reasons,
            warnings=vcr_warnings,
        )


def analyze_reference(path: str, config: Optional[QualityConfig] = None) -> AnalysisResult:
    """Convenience wrapper used by the service layer."""
    return AudioQualityAnalyzer(config).analyze(path)
