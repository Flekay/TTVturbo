"""Video-generation model adapter protocol.

The FastAPI service process never imports a concrete video-generation
framework (diffusers, torch, ...).  Instead it depends on the
:class:`VideoGenerationAdapter` protocol for availability / capability
reporting.  The actual generation runs in a separate worker subprocess
(:mod:`.worker`) which loads the concrete local adapter (diffusers
CogVideoX).

The default :class:`UnavailableVideoGenerationAdapter` reports
unavailable so the base application starts without any generation
dependencies installed and surfaces a clean ``available=false`` to
callers.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    ASPECT_RATIOS,
    RESOLUTIONS_BY_ASPECT_RATIO,
    VideoGenerationUnavailableError,
)

logger = logging.getLogger("ttvturbo.video_generation.adapter")


@runtime_checkable
class VideoGenerationAdapter(Protocol):
    """Protocol every video-generation adapter must satisfy.

    The adapter is consulted **in the FastAPI process** only for
    availability and capability reporting.  The heavy generation runs
    in the worker subprocess (see :mod:`.worker`).
    """

    def available(self) -> bool:
        """True if the adapter can produce results right now."""
        ...

    def capabilities(self) -> dict[str, Any]:
        """Return the capability descriptor for this adapter."""
        ...


class UnavailableVideoGenerationAdapter:
    """Default adapter when no generation model is configured.

    Always reports unavailable so callers get a clean
    :class:`VideoGenerationUnavailableError` instead of a simulated
    result.
    """

    def available(self) -> bool:
        return False

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": False,
            "generation_types": [],
            "aspect_ratios": sorted(ASPECT_RATIOS),
            "resolutions": {
                ratio: [w, h]
                for ratio, (w, h) in RESOLUTIONS_BY_ASPECT_RATIO.items()
            },
            "fps": [],
            "max_duration_seconds": None,
            "max_prompt_length": None,
            "model": None,
            "reasons": ["no video-generation adapter configured"],
        }


def raise_unavailable(reason: str = "video generation is not available") -> None:
    """Raise the standard unavailable error."""
    raise VideoGenerationUnavailableError(reason)
