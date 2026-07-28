"""Ideas Research service.

A reusable backend capability that researches current topics, scores
trends transparently, and generates video ideas and scripts.  No UI.

Pipeline (mirrors the spec workflow)::

    Research Request
      -> aktuelle Quellen suchen        (ResearchProvider)
      -> Quellen normalisieren          (clustering.normalize_source)
      -> Dubletten zusammenführen       (clustering.deduplicate_sources)
      -> Themen clustern                (clustering.cluster_sources)
      -> Trends bewerten                (scoring.score_topic, transparent)
      -> Videoideen erzeugen            (LLMAdapter, INSTRUCT)
      -> Skripte erstellen              (LLMAdapter, THINKING optional)

Model roles:

* INSTRUCT profile: summarise sources, structure, cluster, JSON.
* THINKING profile (optional): complex ideas, angle selection, script
  planning.

Source-of-truth rule: a current fact may only enter a script as a fact
when it has at least one stored source reference.  The service enforces
this in :meth:`_validate_script_sources`.

The service runs synchronously inside :meth:`start_run` in the calling
thread (like :class:`VisualAnalysisService`).  Cancel / retry operate
on the persisted run record.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import uuid
from typing import Any, Iterator, Optional

from ttvturbo.settings import Settings
from ttvturbo.storage_utils import now_iso, validate_uuid

from .clustering import (
    ClusterResult,
    ContradictionResult,
    DedupResult,
    canonical_url,
    cluster_sources,
    deduplicate_sources,
    detect_contradictions,
    normalize_source,
)
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
from .schemas import (
    SCHEMA_VERSION,
    IdeasResearchConflictError,
    IdeasResearchError,
    IdeasResearchNotFoundError,
    IdeasResearchRunStatus,
    IdeasResearchStorageError,
    IdeasResearchUnavailableError,
    IdeasResearchValidationError,
    LLMProfile,
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
    ACTIVE_RUN_STATUSES,
    CANCELLABLE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    make_run_record,
)
from .scoring import (
    ScoringInput,
    parse_time_range_seconds,
    score_topic,
    validate_score_components,
)
from .storage import IdeasResearchStorage

logger = logging.getLogger("ttvturbo.ideas_research.service")

ARTIFACT_TYPE = "ideas_research"
OPERATION = "ideas_research"


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return now_iso()


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class IdeasResearchService:
    """Orchestrates ideas-research runs.

    The service is single-slot: at most one run runs at a time
    (configurable via ``settings.ideas_research_max_concurrent``).  Runs
    run synchronously inside :meth:`start_run` in the calling thread.
    """

    def __init__(
        self,
        storage: IdeasResearchStorage,
        settings: Settings,
        *,
        research_provider: Optional[ResearchProvider] = None,
        llm_adapter: Optional[LLMAdapter] = None,
    ) -> None:
        self.storage = storage
        self.settings = settings
        self.research_provider: ResearchProvider = research_provider or UnavailableResearchProvider()
        self.llm_adapter: LLMAdapter = llm_adapter or UnavailableLLMAdapter()
        self._lock = threading.Lock()
        self._active: set[str] = set()

    # ------------------------------------------------------------------ status
    def runtime_status(self) -> dict:
        """Return the ideas-research capability status."""
        model_id = (self.settings.ideas_research_model_id or "").strip()
        thinking_id = (self.settings.ideas_research_thinking_model_id or "").strip()
        research_ok = bool(self.research_provider.available())
        llm_ok = bool(self.llm_adapter.available())
        reasons: list[str] = []
        if not research_ok:
            reasons.append("research provider unavailable")
        if not model_id:
            reasons.append("no instruct model configured")
        if not llm_ok:
            reasons.append("llm adapter unavailable")
        return {
            "available": research_ok and llm_ok and bool(model_id),
            "research_provider_available": research_ok,
            "llm_available": llm_ok,
            "model_configured": bool(model_id),
            "model": model_id,
            "thinking_model": thinking_id,
            "thinking_enabled": bool(thinking_id),
            "busy": len(self._active) > 0,
            "active_runs": list(self._active),
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }

    # ------------------------------------------------------------------ runs
    def start_run(self, request: dict[str, Any] | ResearchRequest) -> dict:
        """Start a research run for *request*.

        The run runs synchronously and returns the final run record
        (status COMPLETED / FAILED / CANCELED).
        """
        req = _coerce_request(request)
        run_id = _new_uuid()
        now = _now_iso()
        run = make_run_record(
            run_id=run_id,
            request=req.model_dump(),
            created_at=now,
        )
        self.storage.save_run(run)

        with self._lock:
            if len(self._active) >= max(1, self.settings.ideas_research_max_concurrent):
                raise IdeasResearchConflictError(
                    "an ideas-research run is already running"
                )
            self._active.add(run_id)
        try:
            self._run_pipeline(run, req)
        finally:
            with self._lock:
                self._active.discard(run_id)

        return self.storage.load_run(run_id)

    def get_run(self, run_id: str) -> dict:
        validate_uuid(run_id, "run", IdeasResearchValidationError)
        return self.storage.load_run(run_id)

    def list_runs(self, *, status: Optional[str] = None) -> list[dict]:
        runs = list(self.storage.iter_runs())
        if status:
            runs = [r for r in runs if r.get("status") == status]
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs

    def cancel_run(self, run_id: str) -> dict:
        validate_uuid(run_id, "run", IdeasResearchValidationError)
        run = self.storage.load_run(run_id)
        if run.get("status") not in CANCELLABLE_RUN_STATUSES:
            raise IdeasResearchConflictError(
                f"run can only be canceled while active (current: {run.get('status')})"
            )
        run["status"] = IdeasResearchRunStatus.CANCELED
        run["completed_at"] = _now_iso()
        self.storage.save_run(run)
        return run

    def retry_run(self, run_id: str) -> dict:
        validate_uuid(run_id, "run", IdeasResearchValidationError)
        run = self.storage.load_run(run_id)
        if run.get("status") in ACTIVE_RUN_STATUSES:
            raise IdeasResearchConflictError("retry is only allowed for terminal runs")
        if run.get("status") not in (
            IdeasResearchRunStatus.FAILED,
            IdeasResearchRunStatus.CANCELED,
        ):
            raise IdeasResearchConflictError(
                "retry is only allowed for FAILED or CANCELED runs"
            )
        # Reset and re-run synchronously.
        run["status"] = IdeasResearchRunStatus.QUEUED
        run["error"] = None
        run["completed_at"] = None
        run["started_at"] = None
        run["progress"] = 0.0
        run["current_stage"] = None
        run["topic_ids"] = []
        run["idea_ids"] = []
        run["script_ids"] = []
        self.storage.save_run(run)

        req = _coerce_request(run["request"])
        with self._lock:
            self._active.add(run_id)
        try:
            self._run_pipeline(run, req)
        finally:
            with self._lock:
                self._active.discard(run_id)
        return self.storage.load_run(run_id)

    # ------------------------------------------------------------------ topics
    def list_topics(self, run_id: Optional[str] = None) -> list[dict]:
        topics = list(self.storage.iter_topics())
        if run_id:
            topics = [t for t in topics if t.get("run_id") == run_id]
        topics.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return topics

    def get_topic(self, topic_id: str) -> dict:
        validate_uuid(topic_id, "topic", IdeasResearchValidationError)
        return self.storage.load_topic(topic_id)

    def list_sources(self, run_id: str) -> list[dict]:
        validate_uuid(run_id, "run", IdeasResearchValidationError)
        # Touch the run dir (raises NotFound if the run does not exist).
        self.storage.load_run(run_id)
        return list(self.storage.iter_sources(run_id))

    # ------------------------------------------------------------------ ideas
    def list_ideas(self, run_id: Optional[str] = None, topic_id: Optional[str] = None) -> list[dict]:
        ideas = list(self.storage.iter_ideas())
        if run_id:
            ideas = [i for i in ideas if i.get("run_id") == run_id]
        if topic_id:
            ideas = [i for i in ideas if i.get("topic_id") == topic_id]
        ideas.sort(key=lambda i: i.get("created_at", ""), reverse=True)
        return ideas

    def get_idea(self, idea_id: str) -> dict:
        validate_uuid(idea_id, "idea", IdeasResearchValidationError)
        return self.storage.load_idea(idea_id)

    def create_idea(self, topic_id: str, *, request: Optional[dict[str, Any]] = None) -> dict:
        """Generate one :class:`VideoIdea` for *topic_id* via the LLM.

        ``request`` may override ``target_format`` / ``audience`` for
        this idea.  The idea references the topic's sources.
        """
        validate_uuid(topic_id, "topic", IdeasResearchValidationError)
        topic = self.storage.load_topic(topic_id)
        run_id = topic["run_id"]
        run = self.storage.load_run(run_id)
        sources = [self.storage.load_source(run_id, sid) for sid in topic.get("source_ids", [])]
        target_format = (request or {}).get("target_format") or run["request"].get("target_format") or TargetFormat.SHORT.value
        if target_format not in {"SHORT", "LONG"}:
            raise IdeasResearchValidationError(f"unknown target_format: {target_format}")
        audience = (request or {}).get("audience") or ""

        if not self.llm_adapter.available():
            raise IdeasResearchUnavailableError("no LLM configured to generate ideas")
        idea = self._generate_idea(run_id=run_id, topic=topic, sources=sources,
                                   target_format=target_format, audience=audience)
        self.storage.save_idea(idea)
        run = self.storage.load_run(run_id)
        run.setdefault("idea_ids", []).append(idea["id"])
        self.storage.save_run(run)
        return idea

    # ------------------------------------------------------------------ scripts
    def list_scripts(self, idea_id: Optional[str] = None, run_id: Optional[str] = None) -> list[dict]:
        scripts = list(self.storage.iter_scripts())
        if idea_id:
            scripts = [s for s in scripts if s.get("idea_id") == idea_id]
        if run_id:
            scripts = [s for s in scripts if s.get("run_id") == run_id]
        scripts.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return scripts

    def get_script(self, script_id: str) -> dict:
        validate_uuid(script_id, "script", IdeasResearchValidationError)
        return self.storage.load_script(script_id)

    def create_script(self, idea_id: str) -> dict:
        """Generate one :class:`Script` for *idea_id* via the LLM.

        Enforces the source-of-truth rule: every factual statement must
        reference at least one stored source.  Statements flagged
        ``non_factual`` are exempt (transitions, creator opinions).
        """
        validate_uuid(idea_id, "idea", IdeasResearchValidationError)
        idea = self.storage.load_idea(idea_id)
        run_id = idea["run_id"]
        run = self.storage.load_run(run_id)
        # Collect all sources available to this idea (idea sources + topic sources).
        topic = self.storage.load_topic(idea["topic_id"])
        source_ids = list(dict.fromkeys([*idea.get("source_ids", []), *topic.get("source_ids", [])]))
        sources = [self.storage.load_source(run_id, sid) for sid in source_ids]
        known_source_ids = {s["id"] for s in sources}

        if not self.llm_adapter.available():
            raise IdeasResearchUnavailableError("no LLM configured to generate scripts")
        script = self._generate_script(idea=idea, sources=sources, run=run)
        # Validate source references before persisting.
        self._validate_script_sources(script, known_source_ids)
        self.storage.save_script(script)
        run = self.storage.load_run(run_id)
        run.setdefault("script_ids", []).append(script["id"])
        self.storage.save_run(run)
        return script

    def shutdown(self) -> None:
        """No-op (synchronous service).  Satisfies the lifecycle contract."""
        return

    # ==================================================================
    # Pipeline
    # ==================================================================
    def _run_pipeline(self, run: dict, req: ResearchRequest) -> None:
        run_id = run["id"]
        try:
            run["status"] = IdeasResearchRunStatus.RUNNING
            run["started_at"] = _now_iso()
            run["current_stage"] = "search_sources"
            run["progress"] = 5.0
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return

            # 1. Search current sources via the research provider.
            if not self.research_provider.available():
                raise IdeasResearchUnavailableError(
                    "research provider unavailable"
                )
            raw_sources = self.research_provider.search(
                req.topics,
                language=req.language,
                time_range=req.time_range,
                max_topics=req.max_topics,
            )
            if not raw_sources:
                # No sources is a soft failure: complete with empty topics.
                run["current_stage"] = None
                run["progress"] = 100.0
                run["status"] = IdeasResearchRunStatus.COMPLETED
                run["completed_at"] = _now_iso()
                self.storage.save_run(run)
                return

            # 2. Normalise + deduplicate.
            run["current_stage"] = "normalize_sources"
            run["progress"] = 20.0
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return
            fetched_at = _now_iso()
            assigned_topics: dict[str, str] = {}
            normalised: list[Source] = []
            # Track which raw source came from which requested topic so
            # we can carry the assigned topic through normalisation.
            for raw, topic_label in _zip_with_topics(raw_sources, req.topics):
                src = normalize_source(raw, assigned_topic=topic_label, fetched_at=fetched_at)
                normalised.append(src)
                assigned_topics[src.id] = topic_label

            dedup: DedupResult = deduplicate_sources(normalised)
            for src in dedup.sources:
                self.storage.save_source(run_id, src.model_dump())
            if self._is_canceled(run_id):
                return

            # 3. Cluster sources into topics.
            run["current_stage"] = "cluster_topics"
            run["progress"] = 40.0
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return
            sources_by_id = {s.id: s for s in dedup.sources}
            clusters: ClusterResult = cluster_sources(
                dedup.sources, assigned_topics=assigned_topics,
            )
            contradictions: ContradictionResult = detect_contradictions(
                clusters.topics, sources_by_id,
            )

            # 4. Score trends transparently.
            run["current_stage"] = "score_trends"
            run["progress"] = 60.0
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return
            now = _now_dt()
            window = parse_time_range_seconds(req.time_range)
            novelty_by_label = self._compute_novelty(run_id, clusters.topics)
            topic_records: list[dict] = []
            for label, assigned, source_ids in clusters.topics:
                cluster_sources_list = [sources_by_id[sid] for sid in source_ids if sid in sources_by_id]
                inp = ScoringInput(
                    sources=cluster_sources_list,
                    now=now,
                    time_range_seconds=window,
                    target_format=req.target_format,
                    language=req.language,
                    novelty=novelty_by_label.get(label, 1.0),
                    source_count_cap=max(1, self.settings.ideas_research_default_max_topics),
                )
                score = score_topic(inp)
                validate_score_components(score)
                topic_id = _new_uuid()
                topic = Topic(
                    id=topic_id,
                    run_id=run_id,
                    label=label,
                    assigned_topic=assigned,
                    source_ids=source_ids,
                    score=score,
                    created_at=_now_iso(),
                )
                # Back-reference sources to their topic.
                for sid in source_ids:
                    src = sources_by_id.get(sid)
                    if src is not None:
                        src.topic_id = topic_id
                        self.storage.save_source(run_id, src.model_dump())
                self.storage.save_topic(topic.model_dump())
                topic_records.append(topic.model_dump())
            run["topic_ids"] = [t["id"] for t in topic_records]
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return

            # 5. Generate video ideas for the top topics.
            #    This step requires the LLM.  When no LLM is configured,
            #    the run still completes with sources + topics + scores
            #    (ideas can be generated on demand later via create_idea).
            run["current_stage"] = "generate_ideas"
            run["progress"] = 80.0
            self.storage.save_run(run)
            if self._is_canceled(run_id):
                return
            if self.llm_adapter.available():
                max_topics = max(1, min(req.max_topics, len(topic_records)))
                idea_ids: list[str] = []
                for topic in topic_records[:max_topics]:
                    cluster_sources_list = [
                        sources_by_id[sid].model_dump() for sid in topic["source_ids"] if sid in sources_by_id
                    ]
                    idea = self._generate_idea(
                        run_id=run_id,
                        topic=topic,
                        sources=cluster_sources_list,
                        target_format=req.target_format,
                        audience="",
                        contradictions=contradictions.contradicted_topic_labels,
                    )
                    self.storage.save_idea(idea)
                    idea_ids.append(idea["id"])
                run["idea_ids"] = idea_ids
                self.storage.save_run(run)
            else:
                # No LLM — skip idea generation, still complete the run.
                logger.info("LLM unavailable — completing run without ideas (topics+sources only)")
            if self._is_canceled(run_id):
                return

            # 6. Done. (Scripts are created on demand per idea.)
            run["current_stage"] = None
            run["progress"] = 100.0
            run["status"] = IdeasResearchRunStatus.COMPLETED
            run["completed_at"] = _now_iso()
            self.storage.save_run(run)

        except Exception as exc:
            self._fail_run(run, exc)
            raise

    # ------------------------------------------------------------------ cancel
    def _is_canceled(self, run_id: str) -> bool:
        try:
            run = self.storage.load_run(run_id)
        except IdeasResearchNotFoundError:
            return False
        return run.get("status") == IdeasResearchRunStatus.CANCELED

    def _fail_run(self, run: dict, exc: Exception) -> None:
        run["status"] = IdeasResearchRunStatus.FAILED
        run["completed_at"] = _now_iso()
        run["error"] = {
            "code": _error_code(exc),
            "message": str(exc),
            "retryable": isinstance(exc, IdeasResearchUnavailableError),
        }
        self.storage.save_run(run)

    # ------------------------------------------------------------------ novelty
    def _compute_novelty(
        self,
        run_id: str,
        topics: list[tuple[str, str, list[str]]],
    ) -> dict[str, float]:
        """Compute a deterministic novelty score per topic label.

        A topic is novel (1.0) when no previous completed run produced a
        topic with the same label.  When the same label appeared in N
        previous runs, novelty decays as ``1 / (1 + N)``.
        """
        labels = [label for label, _a, _s in topics]
        if not labels:
            return {}
        seen: dict[str, int] = {}
        for prev in self.storage.iter_runs():
            if prev.get("id") == run_id:
                continue
            if prev.get("status") != IdeasResearchRunStatus.COMPLETED:
                continue
            for tid in prev.get("topic_ids", []) or []:
                try:
                    t = self.storage.load_topic(tid)
                except IdeasResearchNotFoundError:
                    continue
                lbl = t.get("label")
                if lbl:
                    seen[lbl] = seen.get(lbl, 0) + 1
        return {lbl: 1.0 / (1.0 + seen.get(lbl, 0)) for lbl in labels}

    # ==================================================================
    # LLM-driven generation
    # ==================================================================
    def _generate_idea(
        self,
        *,
        run_id: str,
        topic: dict,
        sources: list[dict],
        target_format: str,
        audience: str,
        contradictions: Optional[list[str]] = None,
    ) -> dict:
        """Generate one :class:`VideoIdea` for *topic* via the LLM."""
        source_ids = [s["id"] for s in sources]
        prompt = _build_idea_prompt(
            topic_label=topic.get("label", ""),
            assigned_topic=topic.get("assigned_topic", ""),
            target_format=target_format,
            audience=audience,
            language=(self.storage.load_run(run_id).get("request") or {}).get("language", "de"),
            sources=sources,
        )
        profile = LLMProfile.INSTRUCT.value
        if (self.settings.ideas_research_thinking_model_id or "").strip():
            profile = LLMProfile.THINKING.value
        resp = self.llm_adapter.run(prompt, profile=profile, response_json=True)
        data = resp.json or {}
        risks: list[str] = []
        if contradictions and topic.get("label") in contradictions:
            risks.append("Quellen im Thema enthalten widersprüchliche Aussagen.")
        risks.extend(_as_str_list(data.get("risks")))
        idea_id = _new_uuid()
        idea = VideoIdea(
            id=idea_id,
            run_id=run_id,
            topic_id=topic["id"],
            title=str(data.get("title") or topic.get("label", "Videoidee")).strip(),
            angle=str(data.get("angle") or "").strip(),
            hook=str(data.get("hook") or "").strip(),
            format=target_format,
            audience=str(data.get("audience") or audience or "").strip(),
            estimated_length_seconds=float(data.get("estimated_length_seconds") or 0.0),
            source_ids=source_ids,
            risks=risks,
            created_at=_now_iso(),
        )
        return idea.model_dump()

    def _generate_script(self, *, idea: dict, sources: list[dict], run: dict) -> dict:
        """Generate one :class:`Script` for *idea* via the LLM."""
        prompt = _build_script_prompt(
            idea=idea,
            sources=sources,
            language=(run.get("request") or {}).get("language", "de"),
        )
        profile = LLMProfile.INSTRUCT.value
        if (self.settings.ideas_research_thinking_model_id or "").strip():
            profile = LLMProfile.THINKING.value
        resp = self.llm_adapter.run(prompt, profile=profile, response_json=True)
        data = resp.json or {}
        sections = _parse_sections(data.get("sections") or [])
        hook = str(data.get("hook") or idea.get("hook") or "").strip()
        conclusion = str(data.get("conclusion") or "").strip()
        visuals = _as_str_list(data.get("visual_suggestions"))
        # Denormalise source references.
        referenced: list[str] = []
        for sec in sections:
            for stmt in sec.get("statements", []):
                for sid in stmt.get("source_ids", []):
                    if sid not in referenced:
                        referenced.append(sid)
        script_id = _new_uuid()
        script = Script(
            id=script_id,
            idea_id=idea["id"],
            run_id=idea["run_id"],
            hook=hook,
            sections=[ScriptSection(**s) for s in sections],
            conclusion=conclusion,
            visual_suggestions=visuals,
            estimated_speaking_duration_seconds=float(data.get("estimated_speaking_duration_seconds") or 0.0),
            source_references=referenced,
            created_at=_now_iso(),
        )
        return script.model_dump()

    # ------------------------------------------------------------------ validation
    def _validate_script_sources(self, script: dict, known_source_ids: set[str]) -> None:
        """Enforce the source-of-truth rule.

        Every factual statement (``non_factual`` is False) must reference
        at least one stored source id, and every referenced id must be a
        known stored source.  Raises
        :class:`IdeasResearchValidationError` on violation.
        """
        for si, sec in enumerate(script.get("sections", [])):
            for ti, stmt in enumerate(sec.get("statements", [])):
                if stmt.get("non_factual"):
                    continue
                refs = stmt.get("source_ids") or []
                if not refs:
                    raise IdeasResearchValidationError(
                        f"factual statement section[{si}].statements[{ti}] "
                        "has no source reference"
                    )
                unknown = [r for r in refs if r not in known_source_ids]
                if unknown:
                    raise IdeasResearchValidationError(
                        f"statement section[{si}].statements[{ti}] references "
                        f"unknown sources: {unknown}"
                    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_request(request: Any) -> ResearchRequest:
    if isinstance(request, ResearchRequest):
        return request
    if isinstance(request, dict):
        try:
            return ResearchRequest.model_validate(request)
        except Exception as exc:
            raise IdeasResearchValidationError(f"invalid research request: {exc}") from exc
    raise IdeasResearchValidationError("research request must be a dict or ResearchRequest")


def _zip_with_topics(
    raws: list[RawSource], topics: list[str]
) -> list[tuple[RawSource, str]]:
    """Pair each raw source with the first requested topic.

    The static provider does not tag sources by topic; in a real
    provider each :class:`RawSource` would carry its origin topic.  We
    fall back to the first requested topic so clustering still has an
    ``assigned_topic`` to bucket on.
    """
    if not topics:
        return [(r, "") for r in raws]
    primary = topics[0]
    return [(r, primary) for r in raws]


def _error_code(exc: Exception) -> str:
    if isinstance(exc, IdeasResearchValidationError):
        return "IR_VALIDATION"
    if isinstance(exc, IdeasResearchUnavailableError):
        return "IR_UNAVAILABLE"
    if isinstance(exc, IdeasResearchConflictError):
        return "IR_CONFLICT"
    if isinstance(exc, IdeasResearchNotFoundError):
        return "IR_NOT_FOUND"
    if isinstance(exc, IdeasResearchStorageError):
        return "IR_STORAGE"
    return "IR_INTERNAL"


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _parse_sections(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for sec in value:
        if not isinstance(sec, dict):
            continue
        statements = []
        for stmt in sec.get("statements") or []:
            if not isinstance(stmt, dict):
                continue
            statements.append({
                "text": str(stmt.get("text") or "").strip(),
                "source_ids": [str(s) for s in (stmt.get("source_ids") or []) if s],
                "non_factual": bool(stmt.get("non_factual", False)),
            })
        out.append({
            "heading": str(sec.get("heading") or "").strip(),
            "body": str(sec.get("body") or "").strip(),
            "statements": statements,
        })
    return out


def _build_idea_prompt(
    *,
    topic_label: str,
    assigned_topic: str,
    target_format: str,
    audience: str,
    language: str,
    sources: list[dict],
) -> str:
    """Build the LLM prompt for idea generation (Instruct/Thinking)."""
    src_brief = "\n".join(
        f"- [{s['id']}] {s.get('publisher', '')}: {s.get('title', '')} — {s.get('summary', '')[:160]}"
        for s in sources
    ) or "- (keine Quellen)"
    return (
        f"Du bist ein Video-Ideen-Researcher. Sprache: {language}. "
        f"Thema: {assigned_topic or topic_label}. Format: {target_format}. "
        f"Zielgruppe: {audience or 'allgemein'}.\n"
        f"Quellen (nur diese dürfen als Fakt benutzt werden):\n{src_brief}\n\n"
        "Erzeuge EINE Videoidee als JSON mit den Feldern: "
        "title, angle, hook, audience, estimated_length_seconds, risks. "
        "Antworte NUR mit JSON."
    )


def _build_script_prompt(*, idea: dict, sources: list[dict], language: str) -> str:
    """Build the LLM prompt for script generation (Instruct/Thinking)."""
    src_brief = "\n".join(
        f"- [{s['id']}] {s.get('publisher', '')}: {s.get('title', '')} — {s.get('summary', '')[:200]}"
        for s in sources
    ) or "- (keine Quellen)"
    return (
        f"Du bist ein Video-Skript-Autor. Sprache: {language}. "
        f"Idee: {idea.get('title', '')} — Angle: {idea.get('angle', '')} — Hook: {idea.get('hook', '')}.\n"
        f"Quellen (NUR diese dürfen als Fakt benutzt werden; jede faktische "
        f"Aussage muss mindestens eine source_id referenzieren):\n{src_brief}\n\n"
        "Erzeuge ein Skript als JSON mit Feldern: hook, sections (Liste aus "
        "heading, body, statements), conclusion, visual_suggestions, "
        "estimated_speaking_duration_seconds. Jede statement ist ein Objekt "
        "{text, source_ids, non_factual}. non_factual=true für Überleitungen/"
        "Meinungen des Creators. Antworte NUR mit JSON."
    )
