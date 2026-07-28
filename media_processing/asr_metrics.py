"""WER/CER metrics and text normalisation for ASR benchmarking.

Uses :mod:`jiwer` when available. The module degrades gracefully when
jiwer is not installed: :func:`compute_metrics` raises a clear
``AsrMetricsUnavailable`` error instead of silently returning zeros, so
the benchmark worker never produces fake numbers.

Normalisation rules (per the project spec):

* Unicode NFKC normalisation;
* lower-case;
* strip punctuation;
* collapse repeated whitespace;
* preserve umlauts (ä/ö/ü) and ß — no transliteration;
* do NOT translate English gaming terms;
* no aggressive stemming.

The original reference and hypothesis texts are always returned
alongside the normalised forms and a word-level diff so the UI can show
exactly what was changed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional


class AsrMetricsUnavailable(Exception):
    """Raised when jiwer is not installed but metrics are requested."""


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MULTI_WS_RE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Apply the project's standard ASR normalisation."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    # Remove punctuation but keep word characters (incl. umlauts/ß) and
    # whitespace. \w in Unicode mode keeps letters/digits/underscore.
    text = _PUNCT_RE.sub(" ", text)
    text = _MULTI_WS_RE.sub(" ", text)
    return text.strip()


def hypothesis_text(segments: list[dict[str, Any]]) -> str:
    """Concatenate segment texts in order."""
    parts: list[str] = []
    for seg in segments or []:
        t = seg.get("text") if isinstance(seg, dict) else None
        if t:
            parts.append(str(t).strip())
    return " ".join(parts).strip()


@dataclass
class WordDiffOp:
    type: str  # "equal" | "replace" | "delete" | "insert"
    ref: list[str] = field(default_factory=list)
    hyp: list[str] = field(default_factory=list)


@dataclass
class MetricsResult:
    available: bool
    reference_original: str
    hypothesis_original: str
    reference_normalised: str
    hypothesis_normalised: str
    wer: Optional[float] = None
    cer: Optional[float] = None
    mer: Optional[float] = None
    wil: Optional[float] = None
    wip: Optional[float] = None
    hits: Optional[int] = None
    substitutions: Optional[int] = None
    deletions: Optional[int] = None
    insertions: Optional[int] = None
    char_hits: Optional[int] = None
    char_substitutions: Optional[int] = None
    char_deletions: Optional[int] = None
    char_insertions: Optional[int] = None
    word_diff: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _word_diff(reference_words: list[str], hypothesis_words: list[str]) -> list[dict[str, Any]]:
    """Compute a word-level diff using difflib.SequenceMatcher.

    Returns a list of ops with ``type`` in {equal, replace, delete,
    insert} and the corresponding ``ref``/``hyp`` word slices. The
    mapping is the same convention as jiwer's alignment output so the UI
    can colour deletions/insertions/substitutions consistently.
    """
    import difflib  # noqa: PLC0415

    matcher = difflib.SequenceMatcher(a=reference_words, b=hypothesis_words, autojunk=False)
    ops: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append({"type": "equal", "ref": reference_words[i1:i2], "hyp": hypothesis_words[j1:j2]})
        elif tag == "replace":
            ops.append({"type": "replace", "ref": reference_words[i1:i2], "hyp": hypothesis_words[j1:j2]})
        elif tag == "delete":
            ops.append({"type": "delete", "ref": reference_words[i1:i2], "hyp": []})
        elif tag == "insert":
            ops.append({"type": "insert", "ref": [], "hyp": hypothesis_words[j1:j2]})
    return ops


def compute_metrics(reference: Optional[str], hypothesis: Optional[str]) -> MetricsResult:
    """Compute WER/CER and a word diff for one reference/hypothesis pair.

    Semantics:
      * ``reference is None`` means *no ground truth was provided*. The
        result has ``available=False`` and no WER/CER, so the benchmark
        can never declare a winner without a real reference.
      * ``reference == ""`` means an explicit empty reference was given
        (e.g. the user cleared the field). WER/CER are computed and will
        be 1.0 with all-hypothesis-words as insertions.

    If jiwer is not installed, ``available`` is False regardless and the
    WER/CER fields are None; the normalised texts and diff are still
    returned so the UI can show a qualitative comparison.
    """
    ref_orig = reference if reference is not None else ""
    hyp_orig = hypothesis or ""
    ref_norm = normalise_text(ref_orig)
    hyp_norm = normalise_text(hyp_orig)

    no_ground_truth = reference is None

    result = MetricsResult(
        available=False,
        reference_original=ref_orig,
        hypothesis_original=hyp_orig,
        reference_normalised=ref_norm,
        hypothesis_normalised=hyp_norm,
    )

    if no_ground_truth:
        result.error = "no reference text provided"
        # Word diff is still useful for inspection.
        result.word_diff = _word_diff(ref_norm.split(), hyp_norm.split())
        return result

    # Word diff is always available (pure-Python difflib).
    result.word_diff = _word_diff(ref_norm.split(), hyp_norm.split())

    try:
        import jiwer  # type: ignore[import-not-found]  # noqa: PLC0415
    except Exception as exc:
        result.error = f"jiwer not installed: {type(exc).__name__}: {exc}"
        return result

    # Empty reference / hypothesis edge cases: jiwer handles empty strings
    # but we want explicit, documented numbers.
    if not ref_norm and not hyp_norm:
        result.available = True
        result.wer = 0.0
        result.cer = 0.0
        result.mer = 0.0
        result.wil = 0.0
        result.wip = 0.0
        result.hits = 0
        result.substitutions = 0
        result.deletions = 0
        result.insertions = 0
        result.char_hits = 0
        result.char_substitutions = 0
        result.char_deletions = 0
        result.char_insertions = 0
        return result

    try:
        word_out = jiwer.process_words(ref_norm, hyp_norm)
        char_out = jiwer.process_characters(ref_norm, hyp_norm)
        result.available = True
        result.wer = float(word_out.wer)
        result.mer = float(word_out.mer)
        result.wil = float(word_out.wil)
        result.wip = float(word_out.wip)
        result.hits = int(word_out.hits)
        result.substitutions = int(word_out.substitutions)
        result.deletions = int(word_out.deletions)
        result.insertions = int(word_out.insertions)
        result.cer = float(char_out.cer)
        result.char_hits = int(char_out.hits)
        result.char_substitutions = int(char_out.substitutions)
        result.char_deletions = int(char_out.deletions)
        result.char_insertions = int(char_out.insertions)
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"jiwer failed: {type(exc).__name__}: {exc}"
    return result


def rank_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the transparent ranking order for benchmark runs.

    Order:
      1. lowest WER;
      2. fewer insertions on tie;
      3. fewer deletions on tie;
      4. shorter runtime on tie.

    Runs without ground-truth metrics (``metrics.available == False``)
    are excluded from the ranking and returned at the end in their
    original order. The function never invents a winner when no run has
    metrics.
    """
    ranked: list[dict[str, Any]] = []
    unranked: list[dict[str, Any]] = []
    for run in runs:
        metrics = run.get("metrics") or {}
        if metrics.get("available") and metrics.get("wer") is not None:
            ranked.append(run)
        else:
            unranked.append(run)
    ranked.sort(
        key=lambda r: (
            float((r.get("metrics") or {}).get("wer") or 0.0),
            int((r.get("metrics") or {}).get("insertions") or 0),
            int((r.get("metrics") or {}).get("deletions") or 0),
            float(r.get("runtime_seconds") or 0.0),
        )
    )
    return ranked + unranked
