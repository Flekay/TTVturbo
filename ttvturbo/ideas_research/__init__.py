"""TTVturbo Ideas Research backend capability.

On-demand research of current topics, transparent trend scoring, and
generation of video ideas and scripts.  No UI.

Public API
----------
* :class:`IdeasResearchService` — orchestrates runs, topics, ideas and
  scripts.
* :class:`IdeasResearchStorage` — atomic JSON persistence.
* :class:`ResearchAdapter` (Protocol) + :class:`StaticResearchProvider`
  / :class:`UnavailableResearchProvider` — research integration points.
* :class:`LLMAdapter` (Protocol) + :class:`StaticLLMAdapter` /
  :class:`UnavailableLLMAdapter` — LLM integration points (Instruct +
  optional Thinking profile).
* Schemas (:class:`Source`, :class:`Topic`, :class:`TrendScore`,
  :class:`ScoreComponent`, :class:`VideoIdea`, :class:`Script`,
  :class:`ScriptSection`, :class:`ScriptStatement`,
  :class:`ResearchRequest`, :class:`TargetFormat`, :class:`LLMProfile`,
  :class:`Reliability`).
* Clustering helpers (:func:`normalize_source`,
  :func:`deduplicate_sources`, :func:`cluster_sources`,
  :func:`detect_contradictions`, :func:`canonical_url`).
* Scoring helpers (:func:`score_topic`, :func:`validate_score_components`,
  :func:`parse_time_range_seconds`).
"""

from __future__ import annotations

from .schemas import (
    SCHEMA_VERSION,
    LLM_PROFILES,
    RELIABILITY_CONFIDENCE,
    RELIABILITY_VALUES,
    SCORE_COMPONENTS,
    TARGET_FORMATS,
    ACTIVE_RUN_STATUSES,
    CANCELLABLE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    IdeasResearchConflictError,
    IdeasResearchError,
    IdeasResearchNotFoundError,
    IdeasResearchRunStatus,
    IdeasResearchStorageError,
    IdeasResearchUnavailableError,
    IdeasResearchValidationError,
    LLMProfile,
    Reliability,
    ResearchRequest,
    ScoreComponent,
    Script,
    ScriptSection,
    ScriptStatement,
    Source,
    TargetFormat,
    Topic,
    TrendScore,
    VideoIdea,
    make_run_record,
)
from .storage import IdeasResearchStorage
from .providers import (
    LLMAdapter,
    LLMResponse,
    RawSource,
    ResearchProvider,
    StaticLLMAdapter,
    StaticResearchProvider,
    UnavailableLLMAdapter,
    UnavailableResearchProvider,
)
from .clustering import (
    ClusterResult,
    ContradictionResult,
    DedupResult,
    canonical_url,
    cluster_sources,
    deduplicate_sources,
    detect_contradictions,
    normalize_source,
    normalize_title,
    reliability_band,
    title_similarity,
)
from .scoring import (
    ScoringInput,
    parse_time_range_seconds,
    score_topic,
    validate_score_components,
)
from .service import (
    ARTIFACT_TYPE,
    OPERATION,
    IdeasResearchService,
)

__all__ = [
    "SCHEMA_VERSION",
    "ARTIFACT_TYPE",
    "OPERATION",
    "ACTIVE_RUN_STATUSES",
    "CANCELLABLE_RUN_STATUSES",
    "TERMINAL_RUN_STATUSES",
    "LLM_PROFILES",
    "RELIABILITY_CONFIDENCE",
    "RELIABILITY_VALUES",
    "SCORE_COMPONENTS",
    "TARGET_FORMATS",
    "IdeasResearchConflictError",
    "IdeasResearchError",
    "IdeasResearchNotFoundError",
    "IdeasResearchRunStatus",
    "IdeasResearchStorageError",
    "IdeasResearchUnavailableError",
    "IdeasResearchValidationError",
    "LLMProfile",
    "Reliability",
    "ResearchRequest",
    "ScoreComponent",
    "Script",
    "ScriptSection",
    "ScriptStatement",
    "Source",
    "TargetFormat",
    "Topic",
    "TrendScore",
    "VideoIdea",
    "make_run_record",
    "IdeasResearchStorage",
    "LLMAdapter",
    "LLMResponse",
    "RawSource",
    "ResearchProvider",
    "StaticLLMAdapter",
    "StaticResearchProvider",
    "UnavailableLLMAdapter",
    "UnavailableResearchProvider",
    "ClusterResult",
    "ContradictionResult",
    "DedupResult",
    "canonical_url",
    "cluster_sources",
    "deduplicate_sources",
    "detect_contradictions",
    "normalize_source",
    "normalize_title",
    "reliability_band",
    "title_similarity",
    "ScoringInput",
    "parse_time_range_seconds",
    "score_topic",
    "validate_score_components",
    "IdeasResearchService",
]
