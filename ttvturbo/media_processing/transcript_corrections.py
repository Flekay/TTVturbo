"""Editable transcript corrections.

This module adds post-hoc corrections to ASR transcripts on top of the
existing :class:`TranscriptionService` persistence. The original ASR
result (``raw_text``) is immutable; corrections are stored per segment
and the *effective* transcript is computed from
``corrected_text ?? raw_text``.

Design rules (see project spec):

* no second parallel transcript format — ``transcript.json`` is the
  single source of truth and is migrated in place from schema_version 1
  to schema_version 2 on the first correction save;
* the worker (:mod:`media_processing.transcription_worker`) is not
  touched — it keeps writing schema_version 1 with ``segments[].text``;
* old transcripts stay readable; they are only rewritten on the first
  explicit save;
* a lightweight revision history is kept in a ``revisions.json``
  sidecar inside the transcript directory (no new database, no full
  transcript copies).

Schema (schema_version 2, ``transcript.json``)::

    {
      "schema_version": 2,
      "id": "...",
      "source_type": "...",
      "source_id": "...",          # media item id (library item / vod id)
      "audio_artifact": "...",
      "model": "...",
      "device": "...",
      "compute_type": "...",
      "language": "...",
      "language_probability": ...,
      "duration_seconds": ...,
      "created_at": "...",
      "updated_at": "...",
      "revision": 1,
      "correction_status": "RAW",  # RAW | CORRECTED
      "raw_text": "...",           # joined segment raw texts
      "corrected_text": null,      # joined effective text (null until any correction)
      "engine": {"family": "...", "model": "...", "language": "..."},
      "segments": [
        {
          "id": "segment-0",       # stable string id
          "start": ..., "end": ...,
          "raw_text": "...",
          "corrected_text": null,
          "avg_logprob": ..., "no_speech_probability": ...,
          "words": [...]
        }
      ]
    }

A schema_version 1 transcript is interpreted on read as:

* ``text`` → ``raw_text`` (segment and top level);
* ``corrected_text`` → ``null``;
* ``revision`` → ``1``;
* ``correction_status`` → ``RAW``;
* ``segment.id`` → ``segment-{i}``.

The migration to schema_version 2 happens atomically on the first
correction save.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from .schemas import (
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobValidationError,
    TranscriptionStatus,
)
from ..storage_utils import atomic_write_json

logger = logging.getLogger("ttvturbo.media_processing.transcript_corrections")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRANSCRIPT_JSON = "transcript.json"
REVISIONS_JSON = "revisions.json"

SCHEMA_VERSION_V2 = 2
DEFAULT_REVISION = 1

CORRECTION_STATUS_RAW = "RAW"
CORRECTION_STATUS_CORRECTED = "CORRECTED"

# Per-segment corrected_text length cap. Generous enough for any real
# segment, tight enough to prevent pathological payloads.
MAX_CORRECTED_TEXT_LENGTH = 20_000

# Normalise whitespace: collapse runs of whitespace to a single space and
# strip leading/trailing whitespace. We do NOT change the linguistic
# content (no case folding, no punctuation changes).
_WS_RE = re.compile(r"\s+")


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TranscriptCorrectionError(Exception):
    """Base class for transcript-correction errors."""


class TranscriptRevisionConflictError(TranscriptCorrectionError):
    """The expected_revision did not match the stored revision (HTTP 409)."""

    def __init__(self, message: str, current_revision: int, transcript: Optional[dict] = None) -> None:
        super().__init__(message)
        self.current_revision = current_revision
        self.transcript = transcript


# ---------------------------------------------------------------------------
# Normalisation / migration
# ---------------------------------------------------------------------------

def _segment_id(index: int) -> str:
    return f"segment-{index}"


def _normalise_text(value: Any) -> str:
    """Collapse whitespace runs and strip. Returns '' for None/non-str."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _WS_RE.sub(" ", value).strip()


def _join_segment_texts(texts: list[str]) -> str:
    """Join non-empty segment texts with a single space."""
    return " ".join(t for t in texts if t).strip()


def _segment_raw_text(seg: dict) -> str:
    """Return the raw text for a segment, accepting both v1 and v2 shapes."""
    if "raw_text" in seg and seg["raw_text"] is not None:
        return str(seg["raw_text"]).strip()
    if "text" in seg and seg["text"] is not None:
        return str(seg["text"]).strip()
    return ""


def _segment_effective_text(seg: dict) -> str:
    """Return the effective text for a segment (corrected ?? raw)."""
    corrected = seg.get("corrected_text")
    if corrected is not None and str(corrected).strip() != "":
        return str(corrected).strip()
    return _segment_raw_text(seg)


def _normalise_segment(seg: dict, index: int) -> dict:
    """Return a v2-shaped segment view from a raw (v1 or v2) segment dict.

    The original ``words``/``avg_logprob``/``no_speech_probability`` are
    preserved. ``corrected_text`` is ``None`` unless the source already
    carried one (v2).
    """
    try:
        start = float(seg.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    try:
        end = float(seg.get("end", 0.0))
    except (TypeError, ValueError):
        end = 0.0
    raw_text = _segment_raw_text(seg)
    corrected = seg.get("corrected_text")
    if corrected is not None and not isinstance(corrected, str):
        corrected = str(corrected)
    # An empty/whitespace-only corrected_text is treated as None.
    if isinstance(corrected, str) and corrected.strip() == "":
        corrected = None
    seg_id = seg.get("id")
    if not isinstance(seg_id, str):
        seg_id = _segment_id(index)
    return {
        "id": seg_id,
        "start": start,
        "end": end,
        "raw_text": raw_text,
        "corrected_text": corrected,
        "avg_logprob": seg.get("avg_logprob"),
        "no_speech_probability": seg.get("no_speech_probability"),
        "words": seg.get("words") or [],
    }


def _build_engine(payload: dict, job: Optional[dict]) -> dict:
    """Build the engine descriptor from the transcript payload and the
    producing job (best-effort)."""
    family = "whisper"
    if job is not None:
        opts = job.get("options") or {}
        mf = opts.get("model_family")
        if isinstance(mf, str) and mf:
            family = mf
    return {
        "family": family,
        "model": payload.get("model") or "",
        "language": payload.get("language") or None,
    }


def normalise_transcript(payload: dict, job: Optional[dict] = None) -> dict:
    """Return a canonical v2-shaped transcript view from a raw
    ``transcript.json`` payload (v1 or v2).

    This does **not** mutate or persist anything. It is the read path
    used by the service and the API.
    """
    if not isinstance(payload, dict):
        raise MediaJobValidationError("transcript payload is not an object")
    raw_segments = payload.get("segments") or []
    if not isinstance(raw_segments, list):
        raw_segments = []
    segments = [_normalise_segment(seg, i) for i, seg in enumerate(raw_segments)]

    raw_text = _join_segment_texts([s["raw_text"] for s in segments])
    has_any_correction = any(s["corrected_text"] is not None for s in segments)
    corrected_text = _join_segment_texts([_segment_effective_text(s) for s in segments]) if has_any_correction else None
    correction_status = CORRECTION_STATUS_CORRECTED if has_any_correction else CORRECTION_STATUS_RAW

    revision = payload.get("revision")
    if not isinstance(revision, int) or revision < 1:
        revision = DEFAULT_REVISION

    created_at = payload.get("created_at") or _now_iso()
    updated_at = payload.get("updated_at") or created_at

    return {
        "schema_version": SCHEMA_VERSION_V2,
        "id": payload.get("id") or "",
        "source_type": payload.get("source_type"),
        "source_id": payload.get("source_id"),
        "media_item_id": payload.get("source_id"),
        "audio_artifact": payload.get("audio_artifact"),
        "model": payload.get("model"),
        "device": payload.get("device"),
        "compute_type": payload.get("compute_type"),
        "language": payload.get("language"),
        "language_probability": payload.get("language_probability"),
        "duration_seconds": payload.get("duration_seconds"),
        "created_at": created_at,
        "updated_at": updated_at,
        "revision": revision,
        "correction_status": correction_status,
        "raw_text": raw_text,
        "corrected_text": corrected_text,
        "engine": _build_engine(payload, job),
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Effective text contract (for later consumers like Conversation Mining)
# ---------------------------------------------------------------------------

def get_effective_text(transcript: dict) -> str:
    """Return the effective full transcript text.

    Uses ``corrected_text`` when any correction exists, otherwise
    ``raw_text``. Accepts both raw (v1/v2) and normalised transcripts.
    """
    if not isinstance(transcript, dict):
        return ""
    # Fast path: already-normalised v2 view.
    if transcript.get("schema_version") == SCHEMA_VERSION_V2 and "raw_text" in transcript:
        corrected = transcript.get("corrected_text")
        if isinstance(corrected, str) and corrected.strip():
            return corrected
        return transcript.get("raw_text") or ""
    # Slow path: normalise on the fly.
    norm = normalise_transcript(transcript)
    return norm["corrected_text"] or norm["raw_text"]


def get_effective_segments(transcript: dict) -> list[dict]:
    """Return the effective per-segment text as a list of dicts.

    Each entry has ``id``, ``start``, ``end``, ``text`` (effective) and
    ``raw_text``/``corrected_text``. Accepts raw or normalised input.
    """
    if not isinstance(transcript, dict):
        return []
    norm = transcript if transcript.get("schema_version") == SCHEMA_VERSION_V2 and "segments" else normalise_transcript(transcript)
    out: list[dict] = []
    for seg in norm.get("segments") or []:
        out.append({
            "id": seg.get("id"),
            "start": seg.get("start"),
            "end": seg.get("end"),
            "text": _segment_effective_text(seg),
            "raw_text": seg.get("raw_text") or "",
            "corrected_text": seg.get("corrected_text"),
        })
    return out


def effective_contract(transcript: dict, job: Optional[dict] = None) -> dict:
    """Return the slim contract later consumers (Conversation Mining) use::

        {
          "transcript_id": "...",
          "media_item_id": "...",
          "revision": 2,
          "effective_text": "...",
          "effective_segments": [...]
        }
    """
    norm = transcript if transcript.get("schema_version") == SCHEMA_VERSION_V2 and "segments" in transcript else normalise_transcript(transcript, job)
    return {
        "transcript_id": norm.get("id"),
        "media_item_id": norm.get("source_id"),
        "revision": norm.get("revision", DEFAULT_REVISION),
        "effective_text": get_effective_text(norm),
        "effective_segments": get_effective_segments(norm),
    }


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_transcript_v2(path: Path, transcript: dict) -> None:
    """Atomically write a normalised v2 transcript to ``path``."""
    atomic_write_json(path, transcript, MediaJobValidationError, kind="transcript")


def _build_v2_payload_from_raw(raw: dict, job: Optional[dict]) -> dict:
    """Build a v2 transcript payload (with revision=1, no corrections)
    from a raw v1/v2 payload. This is the migration step."""
    norm = normalise_transcript(raw, job)
    # Carry the original v1 top-level fields through so existing
    # consumers (TXT/SRT/VTT exporters, metadata) keep working. We
    # rebuild segments in v2 shape but keep the auxiliary fields.
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "id": norm["id"],
        "source_type": norm["source_type"],
        "source_id": norm["source_id"],
        "audio_artifact": norm["audio_artifact"],
        "model": norm["model"],
        "device": norm["device"],
        "compute_type": norm["compute_type"],
        "language": norm["language"],
        "language_probability": norm["language_probability"],
        "duration_seconds": norm["duration_seconds"],
        "created_at": norm["created_at"],
        "updated_at": norm["updated_at"],
        "revision": DEFAULT_REVISION,
        "correction_status": CORRECTION_STATUS_RAW,
        "raw_text": norm["raw_text"],
        "corrected_text": None,
        "engine": norm["engine"],
        "segments": [
            {
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "raw_text": s["raw_text"],
                "corrected_text": None,
                "avg_logprob": s["avg_logprob"],
                "no_speech_probability": s["no_speech_probability"],
                "words": s["words"],
            }
            for s in norm["segments"]
        ],
    }
    return payload


# ---------------------------------------------------------------------------
# Revision history
# ---------------------------------------------------------------------------

def _read_revisions(revisions_path: Path) -> list[dict]:
    payload = _read_json(revisions_path)
    if not isinstance(payload, dict):
        return []
    revs = payload.get("revisions")
    if not isinstance(revs, list):
        return []
    return [r for r in revs if isinstance(r, dict)]


def _write_revisions(revisions_path: Path, transcription_id: str, revisions: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "transcription_id": transcription_id,
        "revisions": revisions,
    }
    atomic_write_json(revisions_path, payload, MediaJobValidationError, kind="revisions")


def _append_revision(
    revisions_path: Path,
    transcription_id: str,
    revision: int,
    changes: list[dict],
) -> None:
    revisions = _read_revisions(revisions_path)
    revisions.append({
        "revision": revision,
        "created_at": _now_iso(),
        "changes": changes,
    })
    _write_revisions(revisions_path, transcription_id, revisions)


# ---------------------------------------------------------------------------
# Correction store
# ---------------------------------------------------------------------------

class TranscriptCorrectionStore:
    """Reads and writes editable transcript corrections.

    The store operates on a transcript directory (the same directory
    the worker writes ``transcript.json`` into). It is thread-safe via
    a single global lock — corrections are a low-frequency, human-driven
    operation, not a hot path.
    """

    _global_lock = threading.Lock()

    def __init__(self, transcript_dir: Path) -> None:
        self._dir = transcript_dir
        self._transcript_path = transcript_dir / TRANSCRIPT_JSON
        self._revisions_path = transcript_dir / REVISIONS_JSON

    # ------------------------------------------------------------------ read
    def transcript_path(self) -> Path:
        return self._transcript_path

    def revisions_path(self) -> Path:
        return self._revisions_path

    def exists(self) -> bool:
        return self._transcript_path.is_file()

    def load_raw(self) -> dict:
        if not self._transcript_path.is_file():
            raise MediaJobNotFoundError(
                f"transcript file not found: {self._transcript_path.name}"
            )
        raw = _read_json(self._transcript_path)
        if not isinstance(raw, dict):
            raise MediaJobValidationError("transcript file is not a JSON object")
        return raw

    def load(self, job: Optional[dict] = None) -> dict:
        """Load and normalise the transcript to the v2 view."""
        return normalise_transcript(self.load_raw(), job)

    # ------------------------------------------------------------------ save
    def save_corrections(
        self,
        expected_revision: int,
        segment_updates: list[dict],
        job: Optional[dict] = None,
    ) -> dict:
        """Apply a batch of segment corrections atomically.

        ``segment_updates`` is a list of ``{"segment_id": ..., "corrected_text": ...}``.
        ``corrected_text`` may be ``None`` (or empty/whitespace) to
        reset a single segment.

        Returns the normalised v2 transcript after the save.
        """
        if not isinstance(expected_revision, int) or expected_revision < 1:
            raise MediaJobValidationError("expected_revision must be a positive integer")
        if not isinstance(segment_updates, list):
            raise MediaJobValidationError("segments must be a list")
        # Validate updates up front so we never partially mutate the file.
        normalised_updates: list[tuple[str, Optional[str]]] = []
        seen_ids: set[str] = set()
        for upd in segment_updates:
            if not isinstance(upd, dict):
                raise MediaJobValidationError("segment update must be an object")
            seg_id = upd.get("segment_id")
            if seg_id is None:
                raise MediaJobValidationError("segment update is missing segment_id")
            seg_id_str = str(seg_id)
            if seg_id_str in seen_ids:
                raise MediaJobValidationError(f"duplicate segment_id in request: {seg_id_str}")
            seen_ids.add(seg_id_str)
            # Reject any attempt to set raw_text from the client.
            if "raw_text" in upd:
                raise MediaJobValidationError("raw_text cannot be set by the client")
            if "start" in upd or "end" in upd:
                raise MediaJobValidationError("segment start/end cannot be changed")
            corrected = upd.get("corrected_text")
            if corrected is None:
                normalised: Optional[str] = None
            elif isinstance(corrected, str):
                normalised = _normalise_text(corrected)
                if normalised == "":
                    normalised = None
                if normalised is not None and len(normalised) > MAX_CORRECTED_TEXT_LENGTH:
                    raise MediaJobValidationError(
                        f"corrected_text too long (max {MAX_CORRECTED_TEXT_LENGTH})"
                    )
            else:
                raise MediaJobValidationError("corrected_text must be a string or null")
            normalised_updates.append((seg_id_str, normalised))

        with self._global_lock:
            raw = self.load_raw()
            # Decide whether we need to migrate to v2 first.
            schema_version = raw.get("schema_version")
            if schema_version == SCHEMA_VERSION_V2 and "segments" in raw and raw.get("segments"):
                payload = dict(raw)
                # Ensure segments carry v2 fields (defensive).
                segs: list[dict] = []
                for i, seg in enumerate(raw["segments"]):
                    segs.append({
                        "id": seg.get("id") if isinstance(seg.get("id"), str) else _segment_id(i),
                        "start": float(seg.get("start", 0.0)),
                        "end": float(seg.get("end", 0.0)),
                        "raw_text": _segment_raw_text(seg),
                        "corrected_text": seg.get("corrected_text"),
                        "avg_logprob": seg.get("avg_logprob"),
                        "no_speech_probability": seg.get("no_speech_probability"),
                        "words": seg.get("words") or [],
                    })
                payload["segments"] = segs
            else:
                payload = _build_v2_payload_from_raw(raw, job)

            current_revision = payload.get("revision")
            if not isinstance(current_revision, int) or current_revision < 1:
                current_revision = DEFAULT_REVISION
            if current_revision != expected_revision:
                norm_for_conflict = normalise_transcript(payload, job)
                raise TranscriptRevisionConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {current_revision}",
                    current_revision=current_revision,
                    transcript=norm_for_conflict,
                )

            # Index segments by id for lookup.
            by_id: dict[str, dict] = {}
            for seg in payload["segments"]:
                by_id[str(seg.get("id"))] = seg

            changes: list[dict] = []
            changed = False
            for seg_id_str, new_corrected in normalised_updates:
                seg = by_id.get(seg_id_str)
                if seg is None:
                    raise MediaJobValidationError(
                        f"unknown segment_id: {seg_id_str}"
                    )
                before = seg.get("corrected_text")
                # Normalise before for comparison.
                before_norm = before if isinstance(before, str) and before.strip() else None
                if before_norm == new_corrected:
                    # No-op; do not write or record.
                    continue
                seg["corrected_text"] = new_corrected
                changes.append({
                    "segment_id": seg_id_str,
                    "before": before_norm,
                    "after": new_corrected,
                })
                changed = True

            if changed:
                new_revision = current_revision + 1
                payload["revision"] = new_revision
                payload["updated_at"] = _now_iso()
                # Recompute aggregated fields.
                seg_texts_raw = [s.get("raw_text") or "" for s in payload["segments"]]
                seg_texts_eff = [_segment_effective_text(s) for s in payload["segments"]]
                payload["raw_text"] = _join_segment_texts(seg_texts_raw)
                has_correction = any(
                    (s.get("corrected_text") is not None)
                    for s in payload["segments"]
                )
                payload["corrected_text"] = _join_segment_texts(seg_texts_eff) if has_correction else None
                payload["correction_status"] = (
                    CORRECTION_STATUS_CORRECTED if has_correction else CORRECTION_STATUS_RAW
                )
                _write_transcript_v2(self._transcript_path, payload)
                _append_revision(
                    self._revisions_path,
                    payload.get("id") or "",
                    new_revision,
                    changes,
                )
            else:
                # No actual change — do not bump revision or rewrite.
                new_revision = current_revision

            return normalise_transcript(payload, job)

    # ------------------------------------------------------------------ reset
    def reset_segment(
        self,
        segment_id: str,
        expected_revision: Optional[int] = None,
        job: Optional[dict] = None,
    ) -> dict:
        """Reset a single segment's correction to ``None``."""
        return self.save_corrections(
            expected_revision=expected_revision if expected_revision is not None else self._current_revision(),
            segment_updates=[{"segment_id": segment_id, "corrected_text": None}],
            job=job,
        )

    def reset_all_corrections(
        self,
        expected_revision: Optional[int] = None,
        job: Optional[dict] = None,
    ) -> dict:
        """Reset every segment correction to ``None`` and bump the revision."""
        with self._global_lock:
            raw = self.load_raw()
            schema_version = raw.get("schema_version")
            if schema_version == SCHEMA_VERSION_V2 and raw.get("segments"):
                payload = dict(raw)
                # Keep the original segments (with their corrected_text) so
                # we can record the before-values; we clear them below.
                payload["segments"] = [dict(seg) for seg in raw["segments"]]
            else:
                payload = _build_v2_payload_from_raw(raw, job)

            current_revision = payload.get("revision")
            if not isinstance(current_revision, int) or current_revision < 1:
                current_revision = DEFAULT_REVISION
            if expected_revision is not None and expected_revision != current_revision:
                norm_for_conflict = normalise_transcript(payload, job)
                raise TranscriptRevisionConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {current_revision}",
                    current_revision=current_revision,
                    transcript=norm_for_conflict,
                )

            # Record changes only for segments that actually had a correction,
            # then clear corrected_text on every segment.
            changes: list[dict] = []
            for seg in payload["segments"]:
                before = seg.get("corrected_text")
                before_norm = before if isinstance(before, str) and before.strip() else None
                if before_norm is not None:
                    changes.append({
                        "segment_id": str(seg.get("id")),
                        "before": before_norm,
                        "after": None,
                    })
                seg["corrected_text"] = None

            if changes:
                new_revision = current_revision + 1
                payload["revision"] = new_revision
                payload["updated_at"] = _now_iso()
                payload["corrected_text"] = None
                payload["correction_status"] = CORRECTION_STATUS_RAW
                payload["raw_text"] = _join_segment_texts(
                    [s.get("raw_text") or "" for s in payload["segments"]]
                )
                _write_transcript_v2(self._transcript_path, payload)
                _append_revision(
                    self._revisions_path,
                    payload.get("id") or "",
                    new_revision,
                    changes,
                )
            else:
                new_revision = current_revision

            return normalise_transcript(payload, job)

    # ------------------------------------------------------------------ revisions
    def list_revisions(self) -> list[dict]:
        return _read_revisions(self._revisions_path)

    # ------------------------------------------------------------------ helpers
    def _current_revision(self) -> int:
        raw = self.load_raw()
        rev = raw.get("revision")
        if isinstance(rev, int) and rev >= 1:
            return rev
        return DEFAULT_REVISION


# ---------------------------------------------------------------------------
# Validation helpers exposed for the API layer
# ---------------------------------------------------------------------------

def validate_transcription_id(transcription_id: str) -> None:
    """Reject non-UUID transcription ids (path-traversal guard)."""
    try:
        uuid.UUID(transcription_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MediaJobValidationError(
            f"invalid transcription id: {transcription_id!r}"
        ) from exc
