"""Schemas for generic video background removal."""
from __future__ import annotations
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ForegroundMode(str, Enum):
    AUTO_FOREGROUND = "AUTO_FOREGROUND"
    PERSON = "PERSON"
    MANUAL_REGION = "MANUAL_REGION"
    REGION_TRACK = "REGION_TRACK"


class OutputMode(str, Enum):
    TRANSPARENT_VIDEO = "TRANSPARENT_VIDEO"
    ALPHA_MASK = "ALPHA_MASK"
    COMPOSITED_VIDEO = "COMPOSITED_VIDEO"


class BackgroundMode(str, Enum):
    TRANSPARENT = "TRANSPARENT"
    SOLID_COLOR = "SOLID_COLOR"
    BLURRED_ORIGINAL = "BLURRED_ORIGINAL"
    IMAGE_ASSET = "IMAGE_ASSET"
    VIDEO_ASSET = "VIDEO_ASSET"


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def valid(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("region must stay inside frame")
        return self


class BackgroundOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: BackgroundMode = BackgroundMode.TRANSPARENT
    color: str = "#000000"
    image_asset_id: Optional[str] = None
    video_asset_id: Optional[str] = None
    blur_radius: float = Field(default=18.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def references(self):
        if self.mode == BackgroundMode.IMAGE_ASSET and not self.image_asset_id:
            raise ValueError("image_asset_id is required for IMAGE_ASSET")
        if self.mode == BackgroundMode.VIDEO_ASSET and not self.video_asset_id:
            raise ValueError("video_asset_id is required for VIDEO_ASSET")
        if self.mode == BackgroundMode.SOLID_COLOR:
            raw = self.color.lstrip("#")
            if len(raw) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in raw):
                raise ValueError("color must be a hex RGB/RGBA value")
        return self


class StartBackgroundRemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_item_id: str = Field(min_length=1, max_length=128)
    asset_id: Optional[str] = None
    start_us: int = Field(default=0, ge=0)
    end_us: Optional[int] = Field(default=None, gt=0)
    mode: ForegroundMode = ForegroundMode.AUTO_FOREGROUND
    region: Optional[Region] = None
    region_track_artifact_id: Optional[str] = None
    region_track_id: Optional[str] = None
    output_modes: list[OutputMode] = Field(default_factory=lambda: [OutputMode.TRANSPARENT_VIDEO])
    background: BackgroundOptions = Field(default_factory=BackgroundOptions)
    temporal_smoothing: float = Field(default=0.7, ge=0.0, le=1.0)
    edge_refinement: bool = True
    preserve_audio: bool = True
    output_lifecycle: Literal["TEMPORARY", "PERSISTENT"] = "PERSISTENT"

    @model_validator(mode="after")
    def validate_combination(self):
        if self.end_us is not None and self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        if bool(self.region_track_artifact_id) != bool(self.region_track_id):
            raise ValueError("region_track_artifact_id and region_track_id are required together")
        if self.mode == ForegroundMode.MANUAL_REGION and self.region is None:
            raise ValueError("MANUAL_REGION requires region")
        if self.mode == ForegroundMode.REGION_TRACK and not self.region_track_id:
            raise ValueError("REGION_TRACK requires a region track")
        if not self.output_modes:
            raise ValueError("at least one output mode is required")
        # Composition is meaningful only with a non-transparent background.
        if OutputMode.COMPOSITED_VIDEO in self.output_modes and self.background.mode == BackgroundMode.TRANSPARENT:
            raise ValueError("COMPOSITED_VIDEO requires a non-transparent background")
        return self


class BackgroundRemovalResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool
    model_id: str
    source_resolution: list[int]
    output_resolution: list[int]
    duration_seconds: float
    fps: float
    outputs: list[dict[str, Any]]
    effective_options: dict[str, Any]
    error: Optional[str] = None


class BackgroundRemovalError(Exception): pass
class BackgroundRemovalValidationError(BackgroundRemovalError): pass
class BackgroundRemovalNotFoundError(BackgroundRemovalError): pass
class BackgroundRemovalConflictError(BackgroundRemovalError): pass
class BackgroundRemovalUnavailableError(BackgroundRemovalError): pass
