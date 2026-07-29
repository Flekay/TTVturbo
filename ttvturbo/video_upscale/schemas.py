"""Validation schemas for generic video upscaling."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class UpscaleEngine(str, Enum):
    AUTO = "AUTO"
    LANCZOS = "LANCZOS"
    REALESRGAN = "REALESRGAN"


class QualityProfile(str, Enum):
    PREVIEW = "PREVIEW"
    FINAL = "FINAL"


class NormalizedRegion(BaseModel):
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


class UpscaleOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scale: Optional[int] = Field(default=2)
    target_width: Optional[int] = Field(default=None, ge=64, le=15360)
    target_height: Optional[int] = Field(default=None, ge=64, le=15360)
    denoise: bool = False
    deblock: bool = False
    preserve_audio: bool = True
    engine: UpscaleEngine = UpscaleEngine.AUTO
    quality: QualityProfile = QualityProfile.FINAL

    @model_validator(mode="after")
    def dimensions(self) -> "UpscaleOptions":
        if self.scale not in (None, 2, 4):
            raise ValueError("scale must be 2 or 4")
        custom = self.target_width is not None or self.target_height is not None
        if custom and (self.target_width is None or self.target_height is None):
            raise ValueError("target_width and target_height must be supplied together")
        if custom:
            self.scale = None
        return self


class StartUpscaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_item_id: str = Field(min_length=1, max_length=128)
    asset_id: Optional[str] = None
    start_us: int = Field(default=0, ge=0)
    end_us: Optional[int] = Field(default=None, gt=0)
    region: Optional[NormalizedRegion] = None
    region_track_artifact_id: Optional[str] = None
    region_track_id: Optional[str] = None
    output_lifecycle: Literal["TEMPORARY", "PERSISTENT"] = "PERSISTENT"
    options: UpscaleOptions = Field(default_factory=UpscaleOptions)

    @model_validator(mode="after")
    def range_and_track(self) -> "StartUpscaleRequest":
        if self.end_us is not None and self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        if bool(self.region_track_artifact_id) != bool(self.region_track_id):
            raise ValueError("region_track_artifact_id and region_track_id are required together")
        if self.region is not None and self.region_track_id is not None:
            raise ValueError("use either a static region or a region track, not both")
        return self


class UpscaleResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool
    output_file: str
    engine: str
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    source_resolution: list[int]
    output_resolution: list[int]
    duration_seconds: float
    fps: float
    file_size_bytes: int
    effective_options: dict[str, Any]
    error: Optional[str] = None


class VideoUpscaleError(Exception): pass
class VideoUpscaleValidationError(VideoUpscaleError): pass
class VideoUpscaleNotFoundError(VideoUpscaleError): pass
class VideoUpscaleConflictError(VideoUpscaleError): pass
class VideoUpscaleUnavailableError(VideoUpscaleError): pass
