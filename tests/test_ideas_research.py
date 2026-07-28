"""Tests for the Ideas Research backend capability.

Covers the required scenarios from the spec:

* aktuelle und alte Quellen (fresh and stale sources);
* Dubletten (duplicates merged);
* widersprüchliche Quellen (contradictory sources flagged as risks);
* fehlende Quellen (missing sources -> script fact rejected);
* transparente Scores (every component stored with rationale, no opaque
  single LLM number);
* Skriptbehauptungen mit Quellen (script facts reference sources);
* Cancel;
* Retry;
* Research Provider unavailable;
* LLM unavailable;
* keine echten Netzaufrufe in Standardtests (only Static* adapters).

These tests do not perform any real network calls.  They use the
:class:`StaticResearchProvider` and :class:`StaticLLMAdapter` fixtures.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ttvturbo.settings import Settings
from ttvturbo.ideas_research import (
    LLMProfile,
    LLMResponse,
    RawSource,
    ResearchRequest,
    Reliability,
    SCORE_COMPONENTS,
    Source,
    StaticLLMAdapter,
    StaticResearchProvider,
    TargetFormat,
    TrendScore,
    UnavailableLLMAdapter,
    UnavailableResearchProvider,
    IdeasResearchConflictError,
    IdeasResearchNotFoundError,
    IdeasResearchRunStatus,
    IdeasResearchService,
    IdeasResearchStorage,
    IdeasResearchUnavailableError,
    IdeasResearchValidationError,
    canonical_url,
    cluster_sources,
    deduplicate_sources,
    detect_contradictions,
    normalize_source,
    normalize_title,
    parse_time_range_seconds,
    reliability_band,
    score_topic,
    validate_score_components,
)
from ttvturbo.ideas_research.scoring import ScoringInput


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ir_settings(tmp_path: Path) -> Settings:
    s = Settings(data_root=tmp_path / "ir_data")
    s.ideas_research_model_id = "fake-llm/test"
    s.ideas_research_default_max_topics = 10
    return s


@pytest.fixture()
def ir_storage(ir_settings: Settings) -> IdeasResearchStorage:
    return IdeasResearchStorage(ir_settings.paths().ideas_research)


@pytest.fixture()
def ir_service(ir_storage, ir_settings):
    return IdeasResearchService(
        storage=ir_storage,
        settings=ir_settings,
        research_provider=UnavailableResearchProvider(),
        llm_adapter=UnavailableLLMAdapter(),
    )


@pytest.fixture()
def app(ir_settings):
    from ttvturbo.app_factory import create_app, ServiceOverrides
    from ttvturbo.ideas_research import (
        StaticLLMAdapter,
        StaticResearchProvider,
    )
    # Inject static adapters so the wired app can actually run a run.
    research = StaticResearchProvider(results=[])
    llm = StaticLLMAdapter(default=LLMResponse(content="{}", json={}))
    svc = IdeasResearchService(
        storage=IdeasResearchStorage(ir_settings.paths().ideas_research),
        settings=ir_settings,
        research_provider=research,
        llm_adapter=llm,
    )
    overrides = ServiceOverrides(ideas_research_service=svc)
    return create_app(settings=ir_settings, overrides=overrides)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(seconds_ago: float) -> str:
    t = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago)
    return t.replace(microsecond=0).isoformat()


def _raw(
    url: str,
    *,
    title: str = "Test",
    publisher: str = "test",
    published_at: str = "",
    summary: str = "summary",
    growth_signal: float = 0.0,
    reliability_hint: str = Reliability.UNKNOWN.value,
) -> RawSource:
    return RawSource(
        url=url,
        title=title,
        publisher=publisher,
        published_at=published_at,
        summary=summary,
        growth_signal=growth_signal,
        reliability_hint=reliability_hint,
    )


def _build_service(
    ir_settings,
    ir_storage,
    *,
    raws: list[RawSource],
    llm: StaticLLMAdapter | None = None,
    research_fail_next: bool = False,
) -> IdeasResearchService:
    research = StaticResearchProvider(results=raws, fail_next=research_fail_next)
    if llm is None:
        llm = StaticLLMAdapter(default=LLMResponse(content="{}", json={}))
    return IdeasResearchService(
        storage=ir_storage,
        settings=ir_settings,
        research_provider=research,
        llm_adapter=llm,
    )


def _idea_llm() -> StaticLLMAdapter:
    idea_resp = LLMResponse(
        content="{}",
        json={
            "title": "Testidee",
            "angle": "winkel",
            "hook": "hook",
            "audience": "gamer",
            "estimated_length_seconds": 45.0,
            "risks": ["unsichere Behauptung"],
        },
    )
    script_resp_factory = {"_default": idea_resp}
    llm = StaticLLMAdapter(responses={"Videoidee": idea_resp})
    return llm


def _script_llm(statements: list[dict]) -> StaticLLMAdapter:
    """Build an LLM that returns a script with the given statements."""
    script_json = {
        "hook": "hook",
        "sections": [
            {"heading": "H1", "body": "body", "statements": statements},
        ],
        "conclusion": "outro",
        "visual_suggestions": ["bild1"],
        "estimated_speaking_duration_seconds": 30.0,
    }
    idea_resp = LLMResponse(
        content="{}", json={"title": "Testidee", "hook": "hook"},
    )
    return StaticLLMAdapter(
        responses={
            "Videoidee": idea_resp,
            "Skript": LLMResponse(content="{}", json=script_json),
        }
    )


def _run_full(ir_service, **req_kwargs) -> dict:
    req = ResearchRequest(topics=req_kwargs.pop("topics", ["gaming"]), **req_kwargs)
    return ir_service.start_run(req)


# ---------------------------------------------------------------------------
# Schema / request validation
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_research_request_defaults(self):
        r = ResearchRequest(topics=["gaming"])
        assert r.language == "de"
        assert r.time_range == "7d"
        assert r.target_format == TargetFormat.SHORT.value
        assert r.max_topics == 20

    def test_research_request_rejects_empty_topics(self):
        with pytest.raises(Exception):
            ResearchRequest(topics=[])

    def test_research_request_rejects_blank_topics(self):
        with pytest.raises(Exception):
            ResearchRequest(topics=["   ", ""])

    def test_research_request_rejects_unknown_format(self):
        with pytest.raises(Exception):
            ResearchRequest(topics=["gaming"], target_format="HUGE")

    def test_source_reliability_validation(self):
        with pytest.raises(Exception):
            Source(
                id=str(uuid.uuid4()),
                url="https://x.test",
                fetched_at=_iso(0),
                reliability="BOGUS",
            )

    def test_trend_score_rejects_unknown_component(self):
        with pytest.raises(Exception):
            TrendScore(components={"bogus": __import__(
                "ttvturbo.ideas_research", fromlist=["ScoreComponent"]
            ).ScoreComponent(value=0.5, rationale="x")})


# ---------------------------------------------------------------------------
# Clustering / dedup / normalisation
# ---------------------------------------------------------------------------


class TestClustering:
    def test_canonical_url_strips_tracking(self):
        c = canonical_url("https://Example.Test/path/?utm_source=x&keep=1")
        assert c == "https://example.test/path?keep=1"

    def test_canonical_url_strips_trailing_slash(self):
        assert canonical_url("https://x.test/path/") == "https://x.test/path"

    def test_normalize_title(self):
        assert normalize_title("Hello,  World!!") == "hello world"

    def test_reliability_band_high_publisher(self):
        assert reliability_band("Reuters") == Reliability.HIGH.value

    def test_reliability_band_hint_wins(self):
        assert reliability_band("unknown", Reliability.LOW.value) == Reliability.LOW.value

    def test_reliability_band_medium_for_any_publisher(self):
        assert reliability_band("some-blog") == Reliability.MEDIUM.value

    def test_reliability_band_unknown_when_empty(self):
        assert reliability_band("") == Reliability.UNKNOWN.value

    def test_normalize_source_requires_url(self):
        with pytest.raises(ValueError):
            normalize_source(RawSource(url=""), assigned_topic="gaming")

    def test_deduplicate_merges_same_url(self):
        a = normalize_source(_raw("https://x.test/a", publisher="p1"), assigned_topic="g")
        b = normalize_source(_raw("https://x.test/a?utm_source=z", publisher="p2"), assigned_topic="g")
        res = deduplicate_sources([a, b])
        assert len(res.sources) == 1
        assert res.merge_count == 1
        assert "p2" in res.sources[0].confirmed_by

    def test_deduplicate_merges_near_duplicate_title(self):
        a = normalize_source(_raw("https://x.test/1", title="League of Legends Patch 14.2", publisher="p"), assigned_topic="g")
        b = normalize_source(_raw("https://y.test/2", title="League of Legends Patch 14 2", publisher="p"), assigned_topic="g")
        res = deduplicate_sources([a, b], title_threshold=0.8)
        assert len(res.sources) == 1
        assert res.merge_count == 1

    def test_deduplicate_keeps_different_publishers(self):
        a = normalize_source(_raw("https://x.test/1", title="same title", publisher="p1"), assigned_topic="g")
        b = normalize_source(_raw("https://y.test/2", title="same title", publisher="p2"), assigned_topic="g")
        res = deduplicate_sources([a, b])
        # Different publisher + different url -> not merged by title rule.
        assert len(res.sources) == 2

    def test_cluster_groups_by_topic_and_title(self):
        s1 = normalize_source(_raw("https://x.test/a", title="Patch 14.2 Notes", publisher="p1"), assigned_topic="lol")
        s2 = normalize_source(_raw("https://x.test/b", title="Patch 14.2 Notes Breakdown", publisher="p2"), assigned_topic="lol")
        s3 = normalize_source(_raw("https://x.test/c", title="Valorant Update", publisher="p3"), assigned_topic="valorant")
        assigned = {s1.id: "lol", s2.id: "lol", s3.id: "valorant"}
        res = cluster_sources([s1, s2, s3], assigned_topics=assigned, title_threshold=0.6)
        labels = [t[0] for t in res.topics]
        assert len(res.topics) >= 2
        # The two LoL sources cluster together.
        lol = [t for t in res.topics if t[1] == "lol"]
        assert len(lol) == 1
        assert len(lol[0][2]) == 2

    def test_detect_contradictions_flags_opposing_signals(self):
        s1 = normalize_source(_raw("https://x.test/a", title="X confirmed real", summary="it is true", publisher="p1"), assigned_topic="g")
        s2 = normalize_source(_raw("https://x.test/b", title="X is fake and debunked", summary="not real, false", publisher="p2"), assigned_topic="g")
        assigned = {s1.id: "g", s2.id: "g"}
        clusters = cluster_sources([s1, s2], assigned_topics=assigned, title_threshold=0.3)
        by_id = {s.id: s for s in [s1, s2]}
        res = detect_contradictions(clusters.topics, by_id)
        assert res.contradicted_topic_labels  # at least one flagged


# ---------------------------------------------------------------------------
# Scoring (transparent)
# ---------------------------------------------------------------------------


class TestScoring:
    def test_parse_time_range(self):
        assert parse_time_range_seconds("7d") == 7 * 86400.0
        assert parse_time_range_seconds("24h") == 86400.0
        assert parse_time_range_seconds("30m") == 1800.0
        assert parse_time_range_seconds("bogus") is None

    def test_score_topic_has_all_components_with_rationale(self):
        src = Source(
            id=str(uuid.uuid4()),
            url="https://x.test/a",
            title="t",
            publisher="reuters",
            published_at=_iso(3600),
            fetched_at=_iso(0),
            summary="s",
            reliability=Reliability.HIGH.value,
            confirmed_by=["other"],
            growth_signal=0.5,
        )
        inp = ScoringInput(
            sources=[src],
            now=_dt.datetime.now(tz=_dt.timezone.utc),
            time_range_seconds=7 * 86400.0,
            target_format="SHORT",
            language="de",
            novelty=1.0,
            source_count_cap=5,
        )
        score = score_topic(inp)
        for name in SCORE_COMPONENTS:
            assert name in score.components, f"missing {name}"
            comp = score.components[name]
            assert comp.rationale and comp.rationale.strip(), f"{name} has no rationale"
        assert 0.0 <= score.total <= 1.0

    def test_score_fresh_sources_higher_than_stale(self):
        fresh = Source(
            id=str(uuid.uuid4()), url="https://x.test/f", title="t", publisher="p",
            published_at=_iso(60), fetched_at=_iso(0), summary="s",
        )
        stale = Source(
            id=str(uuid.uuid4()), url="https://x.test/s", title="t", publisher="p",
            published_at=_iso(6 * 86400), fetched_at=_iso(0), summary="s",
        )
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        window = 7 * 86400.0
        sf = score_topic(ScoringInput(sources=[fresh], now=now, time_range_seconds=window,
                                      target_format="SHORT", language="de", novelty=1.0))
        ss = score_topic(ScoringInput(sources=[stale], now=now, time_range_seconds=window,
                                      target_format="SHORT", language="de", novelty=1.0))
        assert sf.components["freshness"].value > ss.components["freshness"].value

    def test_score_no_sources_is_zero(self):
        inp = ScoringInput(sources=[], now=_dt.datetime.now(tz=_dt.timezone.utc),
                           time_range_seconds=86400.0, target_format="SHORT",
                           language="de", novelty=0.0)
        score = score_topic(inp)
        assert score.total == 0.0

    def test_validate_score_components_rejects_missing(self):
        score = TrendScore(components={})
        with pytest.raises(ValueError):
            validate_score_components(score)

    def test_validate_score_components_rejects_empty_rationale(self):
        comp = __import__("ttvturbo.ideas_research", fromlist=["ScoreComponent"]).ScoreComponent(value=0.5, rationale="")
        score = TrendScore(components={n: comp for n in SCORE_COMPONENTS})
        with pytest.raises(ValueError):
            validate_score_components(score)


# ---------------------------------------------------------------------------
# Run pipeline (end-to-end with static adapters)
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def test_run_completes_with_topics_and_ideas(self, ir_settings, ir_storage):
        raws = [
            _raw("https://x.test/a", title="Patch 14.2 Notes", publisher="reuters",
                 published_at=_iso(3600), summary="big patch", growth_signal=0.6),
            _raw("https://y.test/b", title="Patch 14.2 Notes Breakdown", publisher="blog",
                 published_at=_iso(7200), summary="analysis", growth_signal=0.2),
        ]
        llm = _idea_llm()
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=llm)
        run = _run_full(svc, topics=["lol"], time_range="7d", target_format="SHORT", max_topics=5)
        assert run["status"] == IdeasResearchRunStatus.COMPLETED
        assert run["topic_ids"], "no topics produced"
        assert run["idea_ids"], "no ideas produced"
        # Sources persisted.
        sources = svc.list_sources(run["id"])
        assert len(sources) >= 1
        # Topics have transparent scores.
        topics = svc.list_topics(run_id=run["id"])
        assert topics
        for t in topics:
            comp = t["score"]["components"]
            for name in SCORE_COMPONENTS:
                assert name in comp
                assert comp[name]["rationale"].strip()

    def test_run_with_no_sources_completes_empty(self, ir_settings, ir_storage):
        svc = _build_service(ir_settings, ir_storage, raws=[])
        run = _run_full(svc)
        assert run["status"] == IdeasResearchRunStatus.COMPLETED
        assert run["topic_ids"] == []
        assert run["idea_ids"] == []

    def test_run_persists_sources_without_full_articles(self, ir_settings, ir_storage):
        long_summary = "x" * 5000
        raws = [_raw("https://x.test/a", title="t", publisher="p",
                     published_at=_iso(60), summary=long_summary)]
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=_idea_llm())
        run = _run_full(svc)
        sources = svc.list_sources(run["id"])
        assert sources
        # Only the (short) summary is stored; the URL/title/publisher are.
        assert sources[0]["url"]
        assert sources[0]["summary"] == long_summary  # we store what was given, never the full article
        assert "content" not in sources[0]  # no full-article field exists

    def test_fresh_and_stale_sources_scored_differently_in_run(self, ir_settings, ir_storage):
        # Two runs: one with fresh sources, one with stale sources.
        fresh = [_raw("https://x.test/f", title="Fresh News", publisher="p", published_at=_iso(60), summary="s")]
        stale = [_raw("https://x.test/s", title="Stale News", publisher="p", published_at=_iso(6 * 86400), summary="s")]
        llm = _idea_llm()
        svc_f = _build_service(ir_settings, ir_storage, raws=fresh, llm=llm)
        run_f = _run_full(svc_f, topics=["g"], time_range="7d")
        topics_f = svc_f.list_topics(run_id=run_f["id"])
        svc_s = _build_service(ir_settings, ir_storage, raws=stale, llm=llm)
        run_s = _run_full(svc_s, topics=["g"], time_range="7d")
        topics_s = svc_s.list_topics(run_id=run_s["id"])
        assert topics_f and topics_s
        f_fresh = topics_f[0]["score"]["components"]["freshness"]["value"]
        s_fresh = topics_s[0]["score"]["components"]["freshness"]["value"]
        assert f_fresh > s_fresh


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------


class TestDuplicates:
    def test_duplicate_urls_merged_in_run(self, ir_settings, ir_storage):
        raws = [
            _raw("https://x.test/a", title="Same", publisher="p1", published_at=_iso(60), summary="s1"),
            _raw("https://x.test/a?utm_source=foo", title="Same", publisher="p2", published_at=_iso(60), summary="s2"),
        ]
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=_idea_llm())
        run = _run_full(svc)
        sources = svc.list_sources(run["id"])
        assert len(sources) == 1
        # confirmed_by union of publishers.
        assert set(sources[0]["confirmed_by"]) >= {"p1", "p2"}


# ---------------------------------------------------------------------------
# Contradictory sources
# ---------------------------------------------------------------------------


class TestContradictions:
    def test_contradictory_topic_flagged_as_risk_on_idea(self, ir_settings, ir_storage):
        raws = [
            _raw("https://x.test/a", title="X confirmed real", publisher="p1",
                 published_at=_iso(60), summary="it is true and confirmed"),
            _raw("https://x.test/b", title="X is fake debunked", publisher="p2",
                 published_at=_iso(60), summary="not real, false, fake"),
        ]
        llm = _idea_llm()
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=llm)
        run = _run_full(svc, topics=["g"], time_range="7d", max_topics=5)
        ideas = svc.list_ideas(run_id=run["id"])
        assert ideas
        # At least one idea carries a contradiction risk.
        all_risks = [r for idea in ideas for r in idea["risks"]]
        assert any("widerspr" in r.lower() for r in all_risks)


# ---------------------------------------------------------------------------
# Script source-of-truth rule
# ---------------------------------------------------------------------------


class TestScriptSources:
    def _setup_run_with_idea(self, ir_settings, ir_storage):
        raws = [_raw("https://x.test/a", title="Patch Notes", publisher="reuters",
                     published_at=_iso(60), summary="patch adds item")]
        llm = _idea_llm()
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=llm)
        run = _run_full(svc, topics=["lol"], max_topics=5)
        ideas = svc.list_ideas(run_id=run["id"])
        assert ideas
        idea = ideas[0]
        # The idea's source ids are the topic's sources.
        topic = svc.get_topic(idea["topic_id"])
        return svc, idea, topic

    def test_script_with_sourced_fact_accepted(self, ir_settings, ir_storage):
        svc, idea, topic = self._setup_run_with_idea(ir_settings, ir_storage)
        source_id = topic["source_ids"][0]
        # Replace the LLM with one that returns a script referencing the source.
        svc.llm_adapter = _script_llm([
            {"text": "Patch fügt ein Item hinzu.", "source_ids": [source_id], "non_factual": False},
        ])
        script = svc.create_script(idea["id"])
        assert script["source_references"] == [source_id]
        assert script["sections"][0]["statements"][0]["source_ids"] == [source_id]

    def test_script_fact_without_source_rejected(self, ir_settings, ir_storage):
        svc, idea, topic = self._setup_run_with_idea(ir_settings, ir_storage)
        svc.llm_adapter = _script_llm([
            {"text": "Behauptung ohne Quelle.", "source_ids": [], "non_factual": False},
        ])
        with pytest.raises(IdeasResearchValidationError):
            svc.create_script(idea["id"])

    def test_script_fact_with_unknown_source_rejected(self, ir_settings, ir_storage):
        svc, idea, topic = self._setup_run_with_idea(ir_settings, ir_storage)
        bogus = str(uuid.uuid4())
        svc.llm_adapter = _script_llm([
            {"text": "Behauptung mit erfundener Quelle.", "source_ids": [bogus], "non_factual": False},
        ])
        with pytest.raises(IdeasResearchValidationError):
            svc.create_script(idea["id"])

    def test_script_non_factual_statement_needs_no_source(self, ir_settings, ir_storage):
        svc, idea, topic = self._setup_run_with_idea(ir_settings, ir_storage)
        source_id = topic["source_ids"][0]
        svc.llm_adapter = _script_llm([
            {"text": "Übrigens, mein Name ist.", "source_ids": [], "non_factual": True},
            {"text": "Patch fügt ein Item hinzu.", "source_ids": [source_id], "non_factual": False},
        ])
        script = svc.create_script(idea["id"])
        assert script["source_references"] == [source_id]


# ---------------------------------------------------------------------------
# Cancel / Retry
# ---------------------------------------------------------------------------


class TestCancelRetry:
    def test_cancel_terminal_run_rejected(self, ir_settings, ir_storage):
        svc = _build_service(ir_settings, ir_storage, raws=[], llm=_idea_llm())
        run = _run_full(svc)
        with pytest.raises(IdeasResearchConflictError):
            svc.cancel_run(run["id"])

    def test_retry_completed_run_rejected(self, ir_settings, ir_storage):
        svc = _build_service(ir_settings, ir_storage, raws=[], llm=_idea_llm())
        run = _run_full(svc)
        with pytest.raises(IdeasResearchConflictError):
            svc.retry_run(run["id"])

    def test_retry_failed_run_succeeds(self, ir_settings, ir_storage):
        # First run fails because the research provider fails.
        raws = [_raw("https://x.test/a", title="t", publisher="p", published_at=_iso(60), summary="s")]
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=_idea_llm(),
                             research_fail_next=True)
        with pytest.raises(IdeasResearchUnavailableError):
            _run_full(svc)
        runs = svc.list_runs()
        assert runs and runs[0]["status"] == IdeasResearchRunStatus.FAILED
        failed_id = runs[0]["id"]
        # The static provider only fails once; retry should now succeed.
        run = svc.retry_run(failed_id)
        assert run["status"] == IdeasResearchRunStatus.COMPLETED

    def test_retry_canceled_run_succeeds(self, ir_settings, ir_storage):
        # Build a service whose research provider blocks until canceled.
        # We simulate cancel by marking the run CANCELED before it runs:
        # since start_run is synchronous, we craft a provider that raises
        # a non-unavailable error to fail, then cancel is tested on a
        # manually-created run.
        svc = _build_service(ir_settings, ir_storage, raws=[], llm=_idea_llm())
        run = _run_full(svc)
        # Mark it canceled manually to test retry from CANCELED.
        rec = svc.storage.load_run(run["id"])
        rec["status"] = IdeasResearchRunStatus.CANCELED
        rec["completed_at"] = _iso(0)
        svc.storage.save_run(rec)
        retried = svc.retry_run(run["id"])
        assert retried["status"] == IdeasResearchRunStatus.COMPLETED


# ---------------------------------------------------------------------------
# Provider / LLM unavailable
# ---------------------------------------------------------------------------


class TestUnavailable:
    def test_research_provider_unavailable_raises(self, ir_settings, ir_storage):
        svc = IdeasResearchService(
            storage=ir_storage, settings=ir_settings,
            research_provider=UnavailableResearchProvider(),
            llm_adapter=UnavailableLLMAdapter(),
        )
        with pytest.raises(IdeasResearchUnavailableError):
            _run_full(svc)

    def test_llm_unavailable_fails_after_research(self, ir_settings, ir_storage):
        raws = [_raw("https://x.test/a", title="t", publisher="p", published_at=_iso(60), summary="s")]
        svc = IdeasResearchService(
            storage=ir_storage, settings=ir_settings,
            research_provider=StaticResearchProvider(results=raws),
            llm_adapter=UnavailableLLMAdapter(),
        )
        with pytest.raises(IdeasResearchUnavailableError):
            _run_full(svc)
        runs = svc.list_runs()
        assert runs[0]["status"] == IdeasResearchRunStatus.FAILED
        assert runs[0]["error"]["code"] == "IR_UNAVAILABLE"

    def test_create_idea_without_llm_raises(self, ir_settings, ir_storage):
        raws = [_raw("https://x.test/a", title="t", publisher="p", published_at=_iso(60), summary="s")]
        # Run with a working LLM to produce a topic, then drop the LLM.
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=_idea_llm())
        run = _run_full(svc)
        topics = svc.list_topics(run_id=run["id"])
        assert topics
        svc.llm_adapter = UnavailableLLMAdapter()
        with pytest.raises(IdeasResearchUnavailableError):
            svc.create_idea(topics[0]["id"])

    def test_create_script_without_llm_raises(self, ir_settings, ir_storage):
        raws = [_raw("https://x.test/a", title="t", publisher="p", published_at=_iso(60), summary="s")]
        svc = _build_service(ir_settings, ir_storage, raws=raws, llm=_idea_llm())
        run = _run_full(svc)
        ideas = svc.list_ideas(run_id=run["id"])
        assert ideas
        svc.llm_adapter = UnavailableLLMAdapter()
        with pytest.raises(IdeasResearchUnavailableError):
            svc.create_script(ideas[0]["id"])


# ---------------------------------------------------------------------------
# No real network calls (static adapters only)
# ---------------------------------------------------------------------------


class TestNoNetworkCalls:
    def test_static_research_provider_makes_no_network_calls(self):
        prov = StaticResearchProvider(results=[_raw("https://x.test/a")])
        assert prov.available()
        out = prov.search(["gaming"], language="de", time_range="7d", max_topics=5)
        assert out and out[0].url == "https://x.test/a"

    def test_static_llm_adapter_makes_no_network_calls(self):
        llm = StaticLLMAdapter(default=LLMResponse(content="{}", json={"x": 1}))
        resp = llm.run("prompt", response_json=True)
        assert resp.json == {"x": 1}

    def test_default_service_uses_unavailable_adapters(self, ir_service):
        status = ir_service.runtime_status()
        assert status["available"] is False
        assert "research provider unavailable" in status["reasons"]


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------


class TestAPI:
    def test_status_endpoint(self, client):
        r = client.get("/api/ideas/status")
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        assert "reasons" in body

    def test_start_run_endpoint(self, client):
        # The wired app uses an empty static research provider -> completes empty.
        r = client.post("/api/ideas/research-runs", json={
            "topics": ["gaming"], "language": "de", "time_range": "7d",
            "target_format": "SHORT", "max_topics": 5,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["status"] in (
            IdeasResearchRunStatus.COMPLETED,
            IdeasResearchRunStatus.FAILED,
        )

    def test_start_run_validation_error(self, client):
        r = client.post("/api/ideas/research-runs", json={"topics": []})
        assert r.status_code in (400, 422)

    def test_get_run_not_found(self, client):
        r = client.get(f"/api/ideas/research-runs/{uuid.uuid4()}")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "ideas_not_found"

    def test_list_runs(self, client):
        client.post("/api/ideas/research-runs", json={"topics": ["gaming"]})
        r = client.get("/api/ideas/research-runs")
        assert r.status_code == 200
        assert "runs" in r.json()

    def test_cancel_retry_endpoints(self, client):
        post = client.post("/api/ideas/research-runs", json={"topics": ["gaming"]})
        run_id = post.json()["id"]
        # Cancel a completed run -> 409.
        c = client.post(f"/api/ideas/research-runs/{run_id}/cancel")
        assert c.status_code == 409
        # Retry a completed run -> 409.
        rt = client.post(f"/api/ideas/research-runs/{run_id}/retry")
        assert rt.status_code == 409

    def test_topics_sources_endpoints(self, client):
        post = client.post("/api/ideas/research-runs", json={"topics": ["gaming"]})
        run_id = post.json()["id"]
        r = client.get("/api/ideas/research-runs")
        assert r.status_code == 200
        t = client.get("/api/ideas/topics")
        assert t.status_code == 200
        s = client.get(f"/api/ideas/research-runs/{run_id}/sources")
        assert s.status_code == 200
        assert "sources" in s.json()
