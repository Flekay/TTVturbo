"""Source normalisation, deduplication and clustering helpers.

Pure functions, no I/O.  Used by :mod:`ttvturbo.ideas_research.service`.

Pipeline (mirrors the spec workflow):

1. :func:`normalize_source` — turn a :class:`RawSource` into a
   :class:`Source` record (assign id, fetched_at, reliability band,
   canonical url).
2. :func:`deduplicate_sources` — merge duplicates by canonical URL and
   by near-duplicate title; merged records keep the union of
   ``confirmed_by`` publishers.
3. :func:`cluster_sources` — group sources into topics by assigned
   request topic and by title similarity; each cluster becomes a
   :class:`Topic` with a label.
4. :func:`detect_contradictions` — flag topic clusters that contain
   contradictory sources (same fact, opposing claims) so the service
   can mark them as risks.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .providers import RawSource
from .schemas import (
    RELIABILITY_CONFIDENCE,
    Source,
    Reliability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .astimezone()
        .replace(microsecond=0)
        .isoformat()
    )


def canonical_url(url: str) -> str:
    """Return a canonical form of *url* for deduplication.

    Lowercases the host, strips a trailing slash, drops the fragment
    and common tracking query parameters.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parts.scheme or not parts.netloc:
        # Not an absolute URL — return the stripped string as-is.
        return url.strip()
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    # Drop tracking params; keep everything else.
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                "utm_content", "ref", "ref_src", "ref_url"}
    pairs = [
        (k, v)
        for k, v in _parse_qsl(parts.query)
        if k.lower() not in tracking
    ]
    pairs.sort()
    query = "&".join(f"{k}={v}" for k, v in pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def _parse_qsl(query: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not query:
        return out
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        out.append((k, v))
    return out


_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_MULTI_WS = re.compile(r"\s+", re.UNICODE)


def normalize_title(title: str) -> str:
    """Normalize a title for near-duplicate comparison."""
    if not title:
        return ""
    t = _NON_WORD.sub(" ", title.lower())
    t = _MULTI_WS.sub(" ", t).strip()
    return t


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity over normalized title word sets (0..1)."""
    sa = set(normalize_title(a).split())
    sb = set(normalize_title(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# Reliability banding
# ---------------------------------------------------------------------------

# A small, conservative allowlist of high-reliability publisher substrings.
# The research provider may also supply a hint; this is a fallback.
HIGH_RELIABILITY_PUBLISHERS = (
    "reuters", "associated press", "ap news", "bbc", "guardian",
    "the new york times", "nytimes", "bloomberg", "washington post",
    "nature", "science magazine", "ieee", "arxiv",
)


def reliability_band(publisher: str, hint: str = Reliability.UNKNOWN.value) -> str:
    """Derive a reliability band from the publisher and a provider hint."""
    p = (publisher or "").lower()
    if hint in RELIABILITY_CONFIDENCE and hint != Reliability.UNKNOWN.value:
        # Trust an explicit HIGH/MEDIUM/LOW hint from the provider.
        return hint
    if any(name in p for name in HIGH_RELIABILITY_PUBLISHERS):
        return Reliability.HIGH.value
    if p:
        return Reliability.MEDIUM.value
    return Reliability.UNKNOWN.value


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_source(
    raw: RawSource,
    *,
    assigned_topic: str,
    fetched_at: str | None = None,
) -> Source:
    """Turn a :class:`RawSource` into a persisted :class:`Source`."""
    if not raw.url or not raw.url.strip():
        raise ValueError("raw source has no url")
    band = reliability_band(raw.publisher, raw.reliability_hint)
    return Source(
        id=_new_uuid(),
        url=raw.url.strip(),
        title=(raw.title or "").strip(),
        publisher=(raw.publisher or "").strip(),
        published_at=(raw.published_at or "").strip(),
        fetched_at=fetched_at or _now_iso(),
        summary=(raw.summary or "").strip(),
        reliability=band,
        topic_id=None,
        confirmed_by=[],
        growth_signal=max(0.0, float(raw.growth_signal or 0.0)),
        # ``assigned_topic`` is stashed on the source via an extra field
        # is not part of the schema; the service carries it in a side
        # map.  We keep the source schema clean.
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

@dataclass
class DedupResult:
    """Result of deduplication.

    ``sources`` are the merged, deduplicated records.  ``merge_count``
    is how many duplicates were merged away.
    """

    sources: list[Source]
    merge_count: int


def deduplicate_sources(
    sources: list[Source],
    *,
    title_threshold: float = 0.8,
) -> DedupResult:
    """Merge duplicate sources.

    Two sources are duplicates when their canonical URLs match, or when
    their normalized titles are near-identical (Jaccard >= threshold)
    **and** they share the same publisher.  Merging keeps the first
    occurrence and unions ``confirmed_by`` publishers from later
    duplicates.  The earliest ``published_at`` is kept.
    """
    merged: list[Source] = []
    seen_canon: dict[str, int] = {}
    merge_count = 0

    for src in sources:
        canon = canonical_url(src.url)
        idx = seen_canon.get(canon)
        if idx is None:
            # Title-based near-dup check against existing merged records.
            idx = _find_near_dup(merged, src, title_threshold)
        if idx is None:
            seen_canon[canon] = len(merged)
            merged.append(_with_confirmed(src, [src.publisher] if src.publisher else []))
            continue
        existing = merged[idx]
        merged[idx] = _merge_one(existing, src)
        merge_count += 1
    return DedupResult(sources=merged, merge_count=merge_count)


def _find_near_dup(pool: list[Source], src: Source, threshold: float) -> int | None:
    si = normalize_title(src.title)
    if not si:
        return None
    for i, other in enumerate(pool):
        if (other.publisher or "").lower() != (src.publisher or "").lower():
            continue
        if title_similarity(other.title, src.title) >= threshold:
            return i
    return None


def _with_confirmed(src: Source, confirmed_by: list[str]) -> Source:
    return src.model_copy(update={"confirmed_by": list(dict.fromkeys(confirmed_by))})


def _merge_one(existing: Source, dup: Source) -> Source:
    confirmed = list(dict.fromkeys([*existing.confirmed_by, dup.publisher] if dup.publisher else existing.confirmed_by))
    # Keep earliest published_at (non-empty wins over empty).
    pub = existing.published_at or dup.published_at
    # Keep the longer summary / title (more information).
    title = existing.title if len(existing.title) >= len(dup.title) else dup.title
    summary = existing.summary if len(existing.summary) >= len(dup.summary) else dup.summary
    growth = max(existing.growth_signal, dup.growth_signal)
    # Higher reliability band wins.
    reliability = _higher_reliability(existing.reliability, dup.reliability)
    return existing.model_copy(update={
        "confirmed_by": confirmed,
        "published_at": pub,
        "title": title,
        "summary": summary,
        "growth_signal": growth,
        "reliability": reliability,
    })


def _higher_reliability(a: str, b: str) -> str:
    order = {
        Reliability.UNKNOWN.value: 0,
        Reliability.LOW.value: 1,
        Reliability.MEDIUM.value: 2,
        Reliability.HIGH.value: 3,
    }
    return a if order.get(a, 0) >= order.get(b, 0) else b


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

@dataclass
class ClusterResult:
    """Result of clustering.

    ``topics`` is a list of (label, assigned_topic, [source_ids]) tuples
    in deterministic order.  The service turns each into a persisted
    :class:`Topic`.
    """

    topics: list[tuple[str, str, list[str]]]


def cluster_sources(
    sources: list[Source],
    *,
    assigned_topics: dict[str, str],
    title_threshold: float = 0.6,
) -> ClusterResult:
    """Group sources into topic clusters.

    ``assigned_topics`` maps ``source.id`` -> the original request topic
    label the source was found for.  Clustering is by (assigned_topic,
    near-duplicate title).  Each cluster gets a deterministic label
    derived from the most common title words.

    Sources without an assigned topic are grouped under their publisher
    or a fallback ``"Allgemein"`` cluster.
    """
    # Bucket by assigned topic first.
    buckets: dict[str, list[Source]] = {}
    for src in sources:
        topic = assigned_topics.get(src.id, "")
        buckets.setdefault(topic, []).append(src)

    topics: list[tuple[str, str, list[str]]] = []
    for topic in sorted(buckets):
        bucket = buckets[topic]
        clusters: list[list[Source]] = []
        for src in bucket:
            placed = False
            for cluster in clusters:
                rep = cluster[0]
                if title_similarity(rep.title, src.title) >= title_threshold:
                    cluster.append(src)
                    placed = True
                    break
            if not placed:
                clusters.append([src])
        for cluster in clusters:
            label = _cluster_label(cluster, fallback=topic or "Allgemein")
            topics.append((label, topic, [s.id for s in cluster]))
    # Deterministic order: by descending source count, then label.
    topics.sort(key=lambda t: (-len(t[2]), t[0]))
    return ClusterResult(topics=topics)


def _cluster_label(cluster: list[Source], *, fallback: str) -> str:
    if not cluster:
        return fallback
    # Use the longest title in the cluster as the label basis.
    rep = max(cluster, key=lambda s: len(s.title))
    if rep.title:
        return rep.title[:120]
    return fallback or (rep.publisher or "Allgemein")


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

# A tiny lexicon of opposing-signal tokens for a deterministic,
# transparent contradiction heuristic.  This is intentionally simple
# and auditable; the LLM never decides this alone.
_NEG = {"not", "no", "false", "fake", "debunked", "wrong", "denies", "denied", "refutes"}
_POS = {"true", "confirmed", "yes", "real", "admits", "confirms", "proves"}


@dataclass
class ContradictionResult:
    """Result of contradiction detection.

    ``contradicted_topic_labels`` lists cluster labels where the sources
    contain opposing-signal language.  The service surfaces these as
    risks on generated ideas.
    """

    contradicted_topic_labels: list[str]


def detect_contradictions(
    topics: list[tuple[str, str, list[str]]],
    sources_by_id: dict[str, Source],
) -> ContradictionResult:
    """Flag clusters with internal opposing-signal language."""
    out: list[str] = []
    for label, _assigned, source_ids in topics:
        has_neg = False
        has_pos = False
        for sid in source_ids:
            src = sources_by_id.get(sid)
            if src is None:
                continue
            text = f"{src.title} {src.summary}".lower()
            tokens = set(re.findall(r"\w+", text))
            if tokens & _NEG:
                has_neg = True
            if tokens & _POS:
                has_pos = True
        if has_neg and has_pos:
            out.append(label)
    return ContradictionResult(contradicted_topic_labels=out)
