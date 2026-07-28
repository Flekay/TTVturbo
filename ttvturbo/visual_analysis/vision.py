"""Vision model adapter for Visual Analysis.

The service never imports a concrete vision framework (torch, transformers,
etc.) directly.  Instead it depends on the :class:`VisionAdapter` protocol.
A real adapter (e.g. a HuggingFace VLM worker) can be plugged in at
construction time; the default :class:`UnavailableVisionAdapter` reports
unavailable so callers can fall back to manual regions or templates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    Box,
    VisualAnalysisUnavailableError,
)

logger = logging.getLogger("ttvturbo.visual_analysis.vision")


@dataclass(frozen=True)
class DetectedRegion:
    """A single region detected by the vision model in one keyframe.

    ``type`` must be one of the :class:`RegionType` values.  ``box`` is
    normalised (0..1).  ``confidence`` is in 0..1.
    """

    type: str
    box: Box
    confidence: float


@runtime_checkable
class VisionAdapter(Protocol):
    """Protocol every vision adapter must satisfy.

    The adapter receives the path to an extracted keyframe image and the
    source resolution.  It returns a list of detected regions.  The
    service validates the returned regions strictly (see
    :mod:`ttvturbo.visual_analysis.tracking`).
    """

    def analyze_keyframe(
        self,
        image_path: Path,
        resolution: tuple[int, int],
    ) -> list[DetectedRegion]:
        ...

    def available(self) -> bool:
        """True if the adapter can produce results right now."""
        ...


class UnavailableVisionAdapter:
    """Default adapter when no vision model is configured.

    Always raises :class:`VisualAnalysisUnavailableError` so the service
    can surface a clean error (or fall back to templates / manual
    regions).
    """

    def analyze_keyframe(
        self,
        image_path: Path,
        resolution: tuple[int, int],
    ) -> list[DetectedRegion]:
        raise VisualAnalysisUnavailableError(
            "no vision model configured for visual analysis"
        )

    def available(self) -> bool:
        return False


class StaticVisionAdapter:
    """A test / fixture adapter that returns canned regions.

    The ``results`` mapping maps a keyframe time (float) to a list of
    :class:`DetectedRegion`.  When a time is not in the map the default
    list is returned (empty if no default set).
    """

    def __init__(
        self,
        results: dict[float, list[DetectedRegion]] | None = None,
        default: list[DetectedRegion] | None = None,
    ) -> None:
        self._results = results or {}
        self._default = default or []

    def analyze_keyframe(
        self,
        image_path: Path,
        resolution: tuple[int, int],
    ) -> list[DetectedRegion]:
        # The time is encoded in the image filename by the keyframe
        # extractor as ``kf_{time:.3f}.jpg``.  Parse it back out so the
        # fixture can return time-specific results.
        time = _parse_time_from_name(image_path)
        if time is not None and time in self._results:
            return list(self._results[time])
        return list(self._default)

    def available(self) -> bool:
        return True


def _parse_time_from_name(path: Path) -> float | None:
    """Extract the keyframe time from ``kf_{time:.3f}.jpg`` filenames."""
    stem = path.stem
    if not stem.startswith("kf_"):
        return None
    try:
        return float(stem[3:])
    except ValueError:
        return None


def detected_region_from_dict(data: dict[str, Any]) -> DetectedRegion:
    """Build a :class:`DetectedRegion` from a raw model-output dict.

    Used by adapters that receive JSON from a worker subprocess.
    """
    box = Box(
        x=float(data["x"]),
        y=float(data["y"]),
        width=float(data["width"]),
        height=float(data["height"]),
    )
    return DetectedRegion(
        type=str(data["type"]),
        box=box,
        confidence=float(data.get("confidence", 1.0)),
    )
