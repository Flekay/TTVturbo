"""Schemas for text-guided video editing and inpainting."""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TextEditMode(str, Enum):
    TEXT_INPAINT = "TEXT_INPAINT"
    TEXT_EDIT = "TEXT_EDIT"


class MaskMode(str, Enum):
    FULL_FRAME = "FULL_FRAME"
    STATIC_REGION = "STATIC_REGION"
    REGION_TRACK = "REGION_TRACK"
    MASK_ASSET = "MASK_ASSET"


class QualityProfile(str, Enum):
    PREVIEW = "PREVIEW"
    FINAL = "FINAL"


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


class TextEditOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_inference_steps: int = Field(default=30, ge=1, le=100)
    guidance_scale: float = Field(default=7.5, ge=0, le=30)
    image_guidance_scale: float = Field(default=1.5, ge=0, le=10)
    strength: float = Field(default=0.85, gt=0, le=1)
    temporal_consistency: float = Field(default=0.2, ge=0, le=0.8)
    seed: Optional[int] = Field(default=None, ge=0, le=2**63 - 1)
    quality: QualityProfile = QualityProfile.FINAL
    preserve_audio: bool = True


class StartVideoTextEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_item_id: str = Field(min_length=1, max_length=128)
    asset_id: Optional[str] = None
    mode: TextEditMode
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = Field(default="", max_length=2000)
    start_us: int = Field(default=0, ge=0)
    end_us: Optional[int] = Field(default=None, gt=0)
    mask_mode: MaskMode = MaskMode.FULL_FRAME
    region: Optional[Region] = None
    region_track_artifact_id: Optional[str] = None
    region_track_id: Optional[str] = None
    mask_asset_id: Optional[str] = None
    options: TextEditOptions = Field(default_factory=TextEditOptions)

    @model_validator(mode="after")
    def combination(self):
        if self.end_us is not None and self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        if bool(self.region_track_artifact_id) != bool(self.region_track_id):
            raise ValueError("region_track_artifact_id and region_track_id are required together")
        if self.mask_mode == MaskMode.STATIC_REGION and self.region is None:
            raise ValueError("STATIC_REGION requires region")
        if self.mask_mode == MaskMode.REGION_TRACK and not self.region_track_id:
            raise ValueError("REGION_TRACK requires a region track")
        if self.mask_mode == MaskMode.MASK_ASSET and not self.mask_asset_id:
            raise ValueError("MASK_ASSET requires mask_asset_id")
        if self.mode == TextEditMode.TEXT_INPAINT and self.mask_mode == MaskMode.FULL_FRAME:
            # Full-frame inpainting is valid, but usually expensive; keep it explicit.
            pass
        return self


class TextEditResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool
    mode: str
    model_id: str
    model_revision: Optional[str] = None
    output_file: str
    source_resolution: list[int]
    output_resolution: list[int]
    duration_seconds: float
    fps: float
    file_size_bytes: int
    seed: int
    effective_options: dict[str, Any]
    error: Optional[str] = None


class VideoTextEditError(Exception): pass
class VideoTextEditValidationError(VideoTextEditError): pass
class VideoTextEditNotFoundError(VideoTextEditError): pass
class VideoTextEditConflictError(VideoTextEditError): pass
class VideoTextEditUnavailableError(VideoTextEditError): pass
