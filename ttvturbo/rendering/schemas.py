"""Schemas for deterministic EditProject rendering."""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class RenderMode(str, Enum):
    PREVIEW = "PREVIEW"
    FINAL = "FINAL"


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: RenderMode = RenderMode.PREVIEW
    video_codec: str = Field(default="libx264", pattern=r"^(libx264|libx265|h264_nvenc|hevc_nvenc)$")
    audio_codec: str = Field(default="aac", pattern=r"^(aac|libopus)$")
    crf: Optional[int] = Field(default=None, ge=0, le=51)
    preset: Optional[str] = Field(default=None, max_length=32)
    preview_max_dimension: int = Field(default=720, ge=240, le=1920)
    include_audio: bool = True


class StartRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=128)
    sequence_id: str = Field(min_length=1, max_length=128)
    commit_id: Optional[str] = None
    settings: RenderSettings = Field(default_factory=RenderSettings)


class RenderResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool
    output_file: str
    width: int
    height: int
    fps: float
    duration_seconds: float
    file_size_bytes: int
    video_codec: str
    audio_codec: Optional[str]
    projection_hash: str
    state_hash: str
    error: Optional[str] = None


class RenderingError(Exception): pass
class RenderingValidationError(RenderingError): pass
class RenderingNotFoundError(RenderingError): pass
class RenderingConflictError(RenderingError): pass
class RenderingUnavailableError(RenderingError): pass
