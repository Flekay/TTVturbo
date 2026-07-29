"""Validation schemas for video region cut (ausschneiden).

Cuts a rectangular area out of a video and produces a new (temporary or
persistent) video that contains only the selected region.  Audio is
preserved from the source unchanged.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class NormalizedRegion(BaseModel):
    """A rectangle in normalized source coordinates (0..1)."""

    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def inside(self) -> "NormalizedRegion":
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("region must remain inside the source frame")
        return self


class CutOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preserve_audio: bool = True
    quality: Literal["PREVIEW", "FINAL"] = "FINAL"


class StartCutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_item_id: str = Field(min_length=1, max_length=128)
    asset_id: Optional[str] = None
    start_us: int = Field(default=0, ge=0)
    end_us: Optional[int] = Field(default=None, gt=0)
    region: NormalizedRegion
    output_lifecycle: Literal["TEMPORARY", "PERSISTENT"] = "TEMPORARY"
    options: CutOptions = Field(default_factory=CutOptions)

    @model_validator(mode="after")
    def range_valid(self) -> "StartCutRequest":
        if self.end_us is not None and self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        return self


class CutResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool
    output_file: str
    source_resolution: list[int]
    output_resolution: list[int]
    duration_seconds: float
    fps: float
    file_size_bytes: int
    effective_options: dict[str, Any]
    error: Optional[str] = None


class VideoCutError(Exception):
    pass


class VideoCutValidationError(VideoCutError):
    pass


class VideoCutNotFoundError(VideoCutError):
    pass


class VideoCutConflictError(VideoCutError):
    pass


class VideoCutUnavailableError(VideoCutError):
    pass
