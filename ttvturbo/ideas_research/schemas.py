"""Schemas for the Ideas Research backend capability.

Pure pydantic models, enums and typed errors.  No FastAPI, no I/O, no
domain imports.  These structures describe the persisted output of an
ideas-research run:

* :class:`Source` — a single normalised source (URL, title, publisher,
  timestamps, summary, reliability signal, topic).
* :class:`Topic` — a cluster of sources with a label and a transparent
  :class:`TrendScore`.
* :class:`TrendScore` — the eight transparent scoring components plus a
  per-component rationale.  **No opaque single LLM number.**
* :class:`VideoIdea` — a generated idea (title, angle, hook, format,
  audience, length, used sources, risks).
* :class:`Script` — a generated script (hook, sections, conclusion,
  visual suggestions, estimated speaking duration, source references
  per factual statement).

The spec mandates that no full foreign articles are stored — only the
short extracted summary.  The spec also mandates that current facts
without a stored source may not enter a script as a fact.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TargetFormat(str, Enum):
    """Video format requested / produced by a research run."""

    SHORT = "SHORT"
    LONG = "LONG"


class LLMProfile(str, Enum):
    """LLM model role.

    INSTRUCT: summarise, structure, cluster, JSON.
    THINKING (optional): complex ideas, angle selection, script planning.
    """

    INSTRUCT = "INSTRUCT"
    THINKING = "THINKING"


TARGET_FORMATS: frozenset[str] = frozenset(t.value for t in TargetFormat)
LLM_PROFILES: frozenset[str] = frozenset(t.value for t in LLMProfile)


# ---------------------------------------------------------------------------
# Run / job status
# ---------------------------------------------------------------------------

class IdeasResearchRunStatus:
    """Lifecycle status of an ideas-research run."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


ACTIVE_RUN_STATUSES = frozenset({
    IdeasResearchRunStatus.QUEUED,
    IdeasResearchRunStatus.RUNNING,
})

TERMINAL_RUN_STATUSES = frozenset({
    IdeasResearchRunStatus.COMPLETED,
    IdeasResearchRunStatus.FAILED,
    IdeasResearchRunStatus.CANCELED,
})

CANCELLABLE_RUN_STATUSES = frozenset({
    IdeasResearchRunStatus.QUEUED,
    IdeasResearchRunStatus.RUNNING,
})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class IdeasResearchError(Exception):
    """Base class for ideas-research errors."""


class IdeasResearchValidationError(IdeasResearchError):
    """Hard validation failure (bad input, invalid request)."""


class IdeasResearchNotFoundError(IdeasResearchError):
    """A run / topic / idea / script with the given id does not exist."""


class IdeasResearchConflictError(IdeasResearchError):
    """A run-level conflict (already running, wrong state for cancel/retry)."""


class IdeasResearchUnavailableError(IdeasResearchError):
    """The research provider or LLM is not available."""


class IdeasResearchStorageError(IdeasResearchError):
    """A persistence failure (corrupt JSON, I/O error)."""


# ---------------------------------------------------------------------------
# Reliability signal
# ---------------------------------------------------------------------------

class Reliability(str, Enum):
    """Coarse reliability band assigned to a source.

    The band is derived from the publisher and cross-source confirmation
    (see :mod:`ttvturbo.ideas_research.scoring`).  It maps to a numeric
    ``source_confidence`` in 0..1.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


RELIABILITY_VALUES: frozenset[str] = frozenset(r.value for r in Reliability)

RELIABILITY_CONFIDENCE: dict[str, float] = {
    Reliability.HIGH.value: 0.9,
    Reliability.MEDIUM.value: 0.6,
    Reliability.LOW.value: 0.3,
    Reliability.UNKNOWN.value: 0.2,
}


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class Source(BaseModel):
    """A single normalised source.

    Only the short extracted ``summary`` is stored — never the full
    foreign article.  ``published_at`` and ``fetched_at`` are ISO-8601
    strings; ``published_at`` may be empty when the publisher does not
    expose a timestamp.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    url: str = Field(min_length=1)
    title: str = ""
    publisher: str = ""
    published_at: str = ""
    fetched_at: str
    summary: str = ""
    reliability: str = Field(default=Reliability.UNKNOWN.value)
    topic_id: Optional[str] = None
    # Optional cross-source confirmation flag set during clustering.
    confirmed_by: list[str] = Field(default_factory=list)
    # Optional growth signal (e.g. view/share delta) provided by the
    # research provider; 0.0 means "no signal".
    growth_signal: float = Field(default=0.0, ge=0.0)
    # Raw engagement metrics from the source platform (views, likes,
    # comments, shares, upvotes, etc.).  Used by the viral_potential
    # scoring component.  Keys are platform-specific; values are ints.
    engagement_metrics: dict[str, int] = Field(default_factory=dict)

    @field_validator("reliability")
    @classmethod
    def _reliability_known(cls, value: str) -> str:
        if value not in RELIABILITY_VALUES:
            raise ValueError(
                f"unknown reliability {value!r}; expected one of {sorted(RELIABILITY_VALUES)}"
            )
        return value


# ---------------------------------------------------------------------------
# Trend score (transparent components)
# ---------------------------------------------------------------------------

# The transparent scoring components required by the spec, plus the
# viral_potential component added for high-viral-score idea generation.
SCORE_COMPONENTS: tuple[str, ...] = (
    "freshness",
    "source_count",
    "cross_source_confirmation",
    "growth_signal",
    "audience_fit",
    "novelty",
    "saturation_penalty",
    "source_confidence",
    "viral_potential",
)


class ScoreComponent(BaseModel):
    """One transparent scoring component with its rationale.

    ``value`` is in 0..1 (except ``saturation_penalty`` which is a
    non-positive penalty in -1..0 applied to the weighted sum).  The
    ``rationale`` explains *why* the value is what it is, in plain
    language, so a human can audit the score.
    """

    model_config = ConfigDict(extra="ignore")

    value: float
    rationale: str = ""
    weight: float = Field(default=1.0, ge=0.0)


class TrendScore(BaseModel):
    """The full transparent trend score for a topic.

    Every component is stored with its value, weight and rationale.
    ``total`` is the weighted combination of the components (with the
    saturation penalty subtracted).  No opaque single LLM number is
    used anywhere in the score.
    """

    model_config = ConfigDict(extra="ignore")

    components: dict[str, ScoreComponent] = Field(default_factory=dict)
    total: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("components")
    @classmethod
    def _components_known(cls, value: dict[str, ScoreComponent]) -> dict[str, ScoreComponent]:
        unknown = set(value) - set(SCORE_COMPONENTS)
        if unknown:
            raise ValueError(
                f"unknown score components: {sorted(unknown)}; "
                f"expected subset of {list(SCORE_COMPONENTS)}"
            )
        return value


# ---------------------------------------------------------------------------
# Topic
# ---------------------------------------------------------------------------

class Topic(BaseModel):
    """A cluster of sources around a single theme.

    ``source_ids`` references the :class:`Source` records that were
    assigned to this cluster.  ``score`` is the transparent
    :class:`TrendScore`.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str
    label: str = Field(min_length=1)
    assigned_topic: str = ""
    source_ids: list[str] = Field(default_factory=list)
    score: TrendScore = Field(default_factory=TrendScore)
    created_at: str


# ---------------------------------------------------------------------------
# Video idea
# ---------------------------------------------------------------------------

class VideoIdea(BaseModel):
    """A generated video idea.

    ``source_ids`` lists the sources the idea is based on.  ``risks``
    lists risky or uncertain claims that a human should verify before
    publishing.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str
    topic_id: str
    title: str = Field(min_length=1)
    angle: str = ""
    hook: str = ""
    format: str = Field(default=TargetFormat.SHORT.value)
    audience: str = ""
    estimated_length_seconds: float = Field(default=0.0, ge=0.0)
    source_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    created_at: str

    @field_validator("format")
    @classmethod
    def _format_known(cls, value: str) -> str:
        if value not in TARGET_FORMATS:
            raise ValueError(
                f"unknown format {value!r}; expected one of {sorted(TARGET_FORMATS)}"
            )
        return value


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------

class ScriptSection(BaseModel):
    """One section of a script.

    ``statements`` carry per-factual-statement source references.  A
    factual statement without any source reference is a violation of
    the spec and is rejected by the service.
    """

    model_config = ConfigDict(extra="ignore")

    heading: str = ""
    body: str = ""
    # Each statement is a factual claim; each must reference at least
    # one source id (enforced by the service, not by the schema, so
    # that non-factual filler sections are still representable).
    statements: list["ScriptStatement"] = Field(default_factory=list)


class ScriptStatement(BaseModel):
    """A factual statement inside a script section with its sources."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    # When True the statement is treated as non-factual filler
    # (transition, opinion of the creator) and does not require sources.
    non_factual: bool = False


class Script(BaseModel):
    """A generated script.

    ``hook`` is the opening.  ``sections`` are the body.  ``conclusion``
    is the closing.  ``visual_suggestions`` are per-section visual
    ideas.  ``estimated_speaking_duration_seconds`` is the estimated
    total speaking duration.  ``source_references`` lists every source
    id referenced anywhere in the script (denormalised for quick
    display).
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    idea_id: str
    run_id: str
    hook: str = ""
    sections: list[ScriptSection] = Field(default_factory=list)
    conclusion: str = ""
    visual_suggestions: list[str] = Field(default_factory=list)
    estimated_speaking_duration_seconds: float = Field(default=0.0, ge=0.0)
    source_references: list[str] = Field(default_factory=list)
    created_at: str


# ---------------------------------------------------------------------------
# Research request
# ---------------------------------------------------------------------------

class ResearchRequest(BaseModel):
    """The request that starts a research run.

    Mirrors the spec example exactly.  ``time_range`` is a short code
    like ``"7d"`` / ``"24h"`` / ``"30d"``.  ``target_format`` is one of
    the :class:`TargetFormat` values.
    """

    model_config = ConfigDict(extra="ignore")

    topics: list[str] = Field(min_length=1)
    language: str = Field(default="de", min_length=1)
    time_range: str = Field(default="7d")
    target_format: str = Field(default=TargetFormat.SHORT.value)
    max_topics: int = Field(default=20, ge=1, le=200)

    @field_validator("target_format")
    @classmethod
    def _format_known(cls, value: str) -> str:
        if value not in TARGET_FORMATS:
            raise ValueError(
                f"unknown target_format {value!r}; expected one of {sorted(TARGET_FORMATS)}"
            )
        return value

    @field_validator("topics")
    @classmethod
    def _topics_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip() for t in value if t and t.strip()]
        if not cleaned:
            raise ValueError("topics must contain at least one non-empty label")
        return cleaned


# ---------------------------------------------------------------------------
# Run record (plain dict persisted as JSON)
# ---------------------------------------------------------------------------

def make_run_record(
    *,
    run_id: str,
    request: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build a new run record dict in the QUEUED state."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": run_id,
        "operation": "ideas_research",
        "request": request,
        "status": IdeasResearchRunStatus.QUEUED,
        "progress": 0.0,
        "current_stage": None,
        "created_at": created_at,
        "started_at": None,
        "completed_at": None,
        "topic_ids": [],
        "idea_ids": [],
        "script_ids": [],
        "error": None,
    }


# Resolve forward references for nested models.
ScriptSection.model_rebuild()
Script.model_rebuild()
