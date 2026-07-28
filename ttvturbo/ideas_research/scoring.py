"""Transparent trend scoring for Ideas Research.

Every component of the trend score is computed by an auditable,
deterministic function and stored with its value, weight and a
human-readable rationale.  **No opaque single LLM number is used.**

Components (see :data:`SCORE_COMPONENTS`):

* ``freshness`` — how recent the sources are within the requested time
  range (1.0 = brand new, 0.0 = stale).
* ``source_count`` — how many distinct sources confirm the topic
  (saturating at a configurable cap).
* ``cross_source_confirmation`` — fraction of sources with at least one
  other independent publisher confirming the same topic.
* ``growth_signal`` — the max provider growth signal across the
  cluster's sources (0..1).
* ``audience_fit`` — fit between the topic and the requested target
  format / language (deterministic heuristic).
* ``novelty`` — how little the topic overlaps with already-known
  topics from previous runs (0..1, 1 = fully novel).
* ``saturation_penalty`` — a non-positive penalty (0..-1) subtracted
  from the weighted sum when a topic looks saturated (many sources,
  low novelty).
* ``source_confidence`` — average reliability confidence of the
  cluster's sources (0..1).

The final ``total`` is the weighted average of the seven 0..1
components (each weight defaults to 1.0) plus the saturation penalty
(clamped to [0, 1]).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from .schemas import (
    RELIABILITY_CONFIDENCE,
    SCORE_COMPONENTS,
    ScoreComponent,
    Source,
    TrendScore,
)


# ---------------------------------------------------------------------------
# Time-range parsing
# ---------------------------------------------------------------------------

def parse_time_range_seconds(time_range: str) -> float | None:
    """Parse a short code like ``"7d"`` / ``"24h"`` / ``"30m"`` into seconds.

    Returns ``None`` when the code cannot be parsed.
    """
    if not time_range:
        return None
    s = time_range.strip().lower()
    if not s:
        return None
    units = {"m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}
    if s[-1] in units and s[:-1].isdigit():
        return float(s[:-1]) * units[s[-1]]
    if s.isdigit():
        # Plain number -> seconds.
        return float(s)
    return None


def _parse_iso(value: str) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Scoring inputs
# ---------------------------------------------------------------------------

@dataclass
class ScoringInput:
    """All inputs needed to score one topic cluster.

    ``sources`` are the cluster's sources.  ``now`` is the reference
    timestamp for freshness.  ``time_range_seconds`` is the requested
    window.  ``target_format`` and ``language`` come from the request.
    ``novelty`` is a 0..1 external novelty score (the service computes
    it from previous runs).  ``source_count_cap`` is the count at which
    ``source_count`` saturates to 1.0.
    """

    sources: list[Source]
    now: _dt.datetime
    time_range_seconds: float | None
    target_format: str
    language: str
    novelty: float
    source_count_cap: int = 5


# ---------------------------------------------------------------------------
# Component functions (each returns a ScoreComponent with rationale)
# ---------------------------------------------------------------------------

def _freshness(inp: ScoringInput) -> ScoreComponent:
    if not inp.sources:
        return ScoreComponent(value=0.0, rationale="keine Quellen")
    if inp.time_range_seconds is None or inp.time_range_seconds <= 0:
        return ScoreComponent(
            value=1.0,
            rationale="kein Zeitfenster vorgegeben, alle Quellen gelten als frisch",
        )
    window = inp.time_range_seconds
    ages: list[float] = []
    for src in inp.sources:
        pub = _parse_iso(src.published_at)
        if pub is None:
            continue
        # Treat naive datetimes as UTC.
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=_dt.timezone.utc)
        now = inp.now
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
        age = (now - pub).total_seconds()
        if age < 0:
            age = 0.0
        ages.append(age)
    if not ages:
        return ScoreComponent(
            value=0.0,
            rationale="keine Quelle mit brauchbarem Veröffentlichungszeitpunkt",
        )
    avg = sum(ages) / len(ages)
    # Linear decay: 0 age -> 1.0, age >= window -> 0.0.
    val = max(0.0, min(1.0, 1.0 - (avg / window)))
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"durchschnittliches Alter {avg:.0f}s bei Fenster {window:.0f}s",
    )


def _source_count(inp: ScoringInput) -> ScoreComponent:
    n = len(inp.sources)
    cap = max(1, inp.source_count_cap)
    val = min(1.0, n / cap)
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"{n} Quellen, Sättigung bei {cap}",
    )


def _cross_source_confirmation(inp: ScoringInput) -> ScoreComponent:
    if not inp.sources:
        return ScoreComponent(value=0.0, rationale="keine Quellen")
    confirmed = 0
    for src in inp.sources:
        publishers = {p.lower() for p in src.confirmed_by if p}
        own = (src.publisher or "").lower()
        others = publishers - {own}
        if others:
            confirmed += 1
    val = confirmed / len(inp.sources)
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"{confirmed}/{len(inp.sources)} Quellen durch weitere Publisher bestätigt",
    )


def _growth_signal(inp: ScoringInput) -> ScoreComponent:
    if not inp.sources:
        return ScoreComponent(value=0.0, rationale="keine Quellen")
    mx = max((s.growth_signal for s in inp.sources), default=0.0)
    val = max(0.0, min(1.0, mx))
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"maximales Wachstumssignal {mx:.2f}",
    )


def _audience_fit(inp: ScoringInput) -> ScoreComponent:
    # Deterministic heuristic: SHORT format favours topics with a hook
    # (short titles, growth signal); LONG format favours topics with
    # more sources (depth).  Language match is binary (1.0 / 0.5).
    if not inp.sources:
        return ScoreComponent(value=0.0, rationale="keine Quellen")
    lang_val = 1.0 if inp.language else 0.5
    if inp.target_format == "SHORT":
        # Shorter average title length -> better short fit.
        avg_len = sum(len(s.title) for s in inp.sources) / max(1, len(inp.sources))
        len_val = max(0.0, min(1.0, 1.0 - (avg_len / 160.0)))
        growth = max((s.growth_signal for s in inp.sources), default=0.0)
        val = 0.4 * lang_val + 0.3 * len_val + 0.3 * growth
        rationale = (
            f"SHORT: Sprache={lang_val:.2f}, Titelkürze={len_val:.2f}, "
            f"Wachstum={growth:.2f}"
        )
    else:
        # LONG favours depth (source count up to cap).
        n = len(inp.sources)
        depth = min(1.0, n / max(1, inp.source_count_cap))
        val = 0.5 * lang_val + 0.5 * depth
        rationale = f"LONG: Sprache={lang_val:.2f}, Tiefe={depth:.2f}"
    val = max(0.0, min(1.0, val))
    return ScoreComponent(value=round(val, 4), rationale=rationale)


def _novelty(inp: ScoringInput) -> ScoreComponent:
    val = max(0.0, min(1.0, inp.novelty))
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"externer Novelty-Score {val:.2f}",
    )


def _saturation_penalty(inp: ScoringInput, novelty_val: float) -> ScoreComponent:
    # Penalty grows when many sources exist but novelty is low.
    n = len(inp.sources)
    cap = max(1, inp.source_count_cap)
    count_ratio = min(1.0, n / cap)
    # penalty in [0, -1]: 0 when novel or few sources, -1 when saturated & stale.
    penalty = -max(0.0, (count_ratio - novelty_val)) * 0.5
    penalty = max(-1.0, min(0.0, penalty))
    return ScoreComponent(
        value=round(penalty, 4),
        rationale=f"Quellenverhältnis {count_ratio:.2f}, Novelty {novelty_val:.2f}",
    )


def _source_confidence(inp: ScoringInput) -> ScoreComponent:
    if not inp.sources:
        return ScoreComponent(value=0.0, rationale="keine Quellen")
    confs = [RELIABILITY_CONFIDENCE.get(s.reliability, 0.2) for s in inp.sources]
    val = sum(confs) / len(confs)
    return ScoreComponent(
        value=round(val, 4),
        rationale=f"mittlere Zuverlässigkeit {val:.2f} über {len(confs)} Quellen",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_topic(inp: ScoringInput) -> TrendScore:
    """Compute the full transparent :class:`TrendScore` for one topic."""
    nov = _novelty(inp)
    components = {
        "freshness": _freshness(inp),
        "source_count": _source_count(inp),
        "cross_source_confirmation": _cross_source_confirmation(inp),
        "growth_signal": _growth_signal(inp),
        "audience_fit": _audience_fit(inp),
        "novelty": nov,
        "saturation_penalty": _saturation_penalty(inp, nov.value),
        "source_confidence": _source_confidence(inp),
    }
    # Weighted combination of the seven 0..1 components, then add the
    # saturation penalty (which is <= 0).
    positive = [c for k, c in components.items() if k != "saturation_penalty"]
    total_weight = sum(c.weight for c in positive)
    if total_weight <= 0:
        weighted = 0.0
    else:
        weighted = sum(c.value * c.weight for c in positive) / total_weight
    total = weighted + components["saturation_penalty"].value
    total = max(0.0, min(1.0, total))
    return TrendScore(
        components={k: c for k, c in components.items()},
        total=round(total, 4),
    )


def validate_score_components(score: TrendScore) -> TrendScore:
    """Validate that a :class:`TrendScore` has all required components.

    Each component must be present with a non-empty rationale so the
    score is fully auditable.
    """
    missing = [c for c in SCORE_COMPONENTS if c not in score.components]
    if missing:
        raise ValueError(f"score missing components: {missing}")
    for name in SCORE_COMPONENTS:
        comp = score.components[name]
        if not comp.rationale or not comp.rationale.strip():
            raise ValueError(f"score component {name!r} has no rationale")
    return score
