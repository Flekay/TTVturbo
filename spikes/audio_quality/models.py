"""Data models and configuration for the audio quality analyzer.

All thresholds are gathered in :class:`QualityConfig` so the classification
rules can be tuned in a single place.  The dataclasses defined here are the
single source of truth for the JSON output produced by ``analyze.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Quality(str, Enum):
    """Top-level technical quality classification."""

    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


@dataclass
class QualityConfig:
    """Single, tunable configuration bundle for the analyzer.

    Thresholds are intentionally conservative defaults for voice clone
    reference recordings.  They are NOT derived from a trained model and
    must be re-validated on real data (see REPORT.md).
    """

    # --- silence / framing -------------------------------------------------
    frame_ms: float = 20.0
    hop_ms: float = 10.0
    silence_threshold_dbfs: float = -45.0

    # --- clipping ----------------------------------------------------------
    clipping_magnitude: float = 0.999

    # --- noise floor / SNR -------------------------------------------------
    noise_floor_percentile: float = 10.0  # leisesten 10 % nichtleerer Frames

    # --- dropouts ----------------------------------------------------------
    dropout_min_duration_ms: float = 20.0
    dropout_sample_abs: float = 1e-4  # |sample| <= Wert gilt als "Fast-Null"

    # --- reference duration (voice clone) ----------------------------------
    reference_min_seconds: float = 5.0
    reference_max_seconds: float = 12.0
    absolute_min_seconds: float = 1.0

    # --- classification thresholds ----------------------------------------
    near_silence_ratio: float = 0.99  # total_silence_ratio >= -> REJECT
    severe_clipping_ratio: float = 1e-3  # clipping_sample_ratio >= -> REJECT
    isolated_clipping_ratio: float = 1e-5  # >= -> REVIEW
    low_snr_db: float = 15.0  # < -> REVIEW
    good_snr_db: float = 25.0  # >= -> contributes to EXCELLENT
    long_silence_ms: float = 500.0  # leading/trailing > -> REVIEW
    severe_dc_offset: float = 0.05  # |dc| >= -> REVIEW

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TechnicalMetadata:
    """Purely descriptive metadata of the original file."""

    path: str
    sample_rate: int
    channels: int
    frame_count: int
    duration_seconds: float
    subtype: Optional[str] = None
    format: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LevelMetrics:
    peak_dbfs: Optional[float]
    rms_dbfs: Optional[float]
    dc_offset: float
    clipping_sample_count: int
    clipping_sample_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SilenceMetrics:
    leading_silence_ms: float
    trailing_silence_ms: float
    total_silence_ratio: float
    voice_ratio: float
    frame_count_total: int
    frame_count_silent: int
    frame_count_active: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NoiseMetrics:
    estimated_noise_floor_dbfs: Optional[float]
    estimated_snr_db: Optional[float]
    active_frames_used: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DropoutMetrics:
    dropout_count: int
    dropout_total_ms: float
    longest_dropout_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrityMetrics:
    has_nan: bool
    has_infinity: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceCloneReference:
    eligible: bool
    quality: Quality
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "quality": self.quality.value,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


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
    voice_clone_reference: VoiceCloneReference = field(default_factory=lambda: VoiceCloneReference(False, Quality.REJECT))
    config: QualityConfig = field(default_factory=QualityConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "technical": self.technical.to_dict(),
            "levels": self.levels.to_dict(),
            "silence": self.silence.to_dict(),
            "noise": self.noise.to_dict(),
            "dropouts": self.dropouts.to_dict(),
            "integrity": self.integrity.to_dict(),
            "quality": self.quality.value,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "voice_clone_reference": self.voice_clone_reference.to_dict(),
            "config": self.config.to_dict(),
        }
