"""Conversation Mining service.

Analyzes the effective transcript (corrected_text ?? raw_text) and
decomposes it into coherent conversation sections that may later be
evaluated as clip candidates. Conversation Mining does NOT produce
final clips, scores or review-queue entries.

Input contract
--------------
The service accesses the transcript exclusively through the central
:class:`TranscriptionService` effective-text contract
(``get_transcript_contract``). It never reads transcript files via
free file paths and never decides between raw and corrected text
itself.

Pipeline
--------

    Twitch-URL -> Download -> Audio -> Transcription -> Conversation Mining

Model integration
-----------------
A single worker subprocess (``conversation_mining_worker``) loads the
configured HuggingFace text model, processes blocks sequentially,
writes block results and the final deduplicated conversation list.
The FastAPI process never imports transformers/torch. The shared
cross-process GPU lock is reused. When no model is configured the
service reports ``UNAVAILABLE`` and the pipeline step fails
controllably.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from ttvturbo.settings import Settings
from ttvturbo.storage_utils import atomic_write_json

from .gpu_lock import GpuLock, GpuLockBusyError, GpuLockError, GpuLockOwner
from .schemas import (
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobValidationError,
    TranscriptionStatus,
)
from .transcription import (
    ARTIFACTS_SUBDIR,
    TRANSCRIPTS_SUBDIR,
    TranscriptionService,
)

logger = logging.getLogger("ttvturbo.media_processing.conversation_mining")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
MINING_SUBDIR = "conversation_mining"
RUN_JSON = "run.json"
BLOCKS_JSON = "blocks.json"
RAW_RESPONSES_JSON = "raw_responses.json"
CONVERSATIONS_JSON = "conversations.json"
WORKER_JOB_JSON = "worker_job.json"
WORKER_LOG = "worker.log"

ORCHESTRATOR_POLL_SECONDS = 2.0
KILL_GRACE_SECONDS = 5.0

# Mining config version — bumped when block-building or prompt defaults
# change so idempotency correctly invalidates old results.
MINING_CONFIG_VERSION = 1

# Dedup overlap threshold (fraction of segment span overlap).
DEDUP_OVERLAP_THRESHOLD = 0.60

# Boundary cleanup: max segments to expand on each side.
BOUNDARY_EXPAND_SEGMENTS = 2
# Boundary cleanup: max seconds to expand on each side.
BOUNDARY_EXPAND_SECONDS = 10.0

# Text length limits for model output validation.
MAX_TITLE_LENGTH = 200
MAX_SUMMARY_LENGTH = 1000
MAX_TRANSCRIPT_EXCERPT_LENGTH = 2000

# Allowed categories (fixed set).
CATEGORIES = (
    "REACTION",
    "STORY",
    "OPINION",
    "EXPLANATION",
    "JOKE",
    "ARGUMENT",
    "QUESTION",
    "GAMEPLAY_EVENT",
    "CHAT_INTERACTION",
    "OTHER",
)

# Allowed signals (fixed set).
SIGNALS = (
    "emotion",
    "surprise",
    "humor",
    "controversy",
    "clear_context",
    "self_contained",
    "strong_opening",
    "strong_ending",
    "payoff",
    "story_progression",
    "chat_interaction",
    "gameplay_context",
)

# HTML/script sanitisation patterns.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Status enums
# ---------------------------------------------------------------------------


class MiningRunStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    STALE = "STALE"


ACTIVE_MINING_STATUSES = frozenset({
    MiningRunStatus.QUEUED,
    MiningRunStatus.RUNNING,
})

TERMINAL_MINING_STATUSES = frozenset({
    MiningRunStatus.COMPLETED,
    MiningRunStatus.FAILED,
    MiningRunStatus.CANCELED,
    MiningRunStatus.STALE,
})

CANCELLABLE_MINING_STATUSES = frozenset({
    MiningRunStatus.QUEUED,
    MiningRunStatus.RUNNING,
})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class ConversationMiningError(Exception):
    """Base class for conversation-mining errors."""


class ConversationMiningValidationError(ConversationMiningError):
    """Hard validation failure (bad input, missing transcript)."""


class ConversationMiningNotFoundError(ConversationMiningError):
    """A mining run with the given id does not exist."""


class ConversationMiningConflictError(ConversationMiningError):
    """A mining-level conflict (already running, wrong state)."""


class ConversationMiningUnavailableError(ConversationMiningError):
    """The mining model/service is not available."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _validate_uuid(value: str) -> str:
    """Validate that *value* is a UUID string (path-traversal guard)."""
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ConversationMiningValidationError(f"invalid id: {value!r}") from exc
    return value


def _sanitize_text(text: str) -> str:
    """Strip HTML tags and script markers from *text*."""
    if not isinstance(text, str):
        return ""
    if _SCRIPT_RE.search(text):
        text = _SCRIPT_RE.sub("&lt;script", text)
    return _HTML_TAG_RE.sub("", text)


# ---------------------------------------------------------------------------
# Block building
# ---------------------------------------------------------------------------


def build_blocks(
    segments: list[dict],
    *,
    target_seconds: float,
    max_seconds: float,
    overlap_seconds: float,
    pause_seconds: float,
) -> list[dict]:
    """Build deterministic working blocks from effective segments.

    Blocks always begin and end on segment boundaries. Long pauses
    between segments trigger a break. Overlap is achieved by starting
    the next block a few segments before the previous block ended.

    Each block dict has::

        {
          "block_id": "block-0",
          "start": 0.0,
          "end": 90.0,
          "start_segment_index": 0,
          "end_segment_index": 5,
          "segment_ids": ["segment-0", ...],
        }
    """
    if not segments:
        return []
    blocks: list[dict] = []
    n = len(segments)
    i = 0
    block_num = 0
    while i < n:
        start_idx = i
        block_start = float(segments[i].get("start") or 0.0)
        # Extend the block until we hit the target duration, a long pause,
        # or the max block duration.
        j = i
        while j < n:
            seg = segments[j]
            seg_end = float(seg.get("end") or 0.0)
            duration = seg_end - block_start
            if duration > max_seconds and j > start_idx:
                j -= 1  # don't exceed max
                break
            # Check pause between this segment and the next.
            if j + 1 < n:
                next_start = float(segments[j + 1].get("start") or 0.0)
                pause = next_start - seg_end
                if pause >= pause_seconds and duration >= target_seconds * 0.5:
                    break  # natural break at long pause
            if duration >= target_seconds:
                break
            j += 1
        end_idx = min(j, n - 1)
        block_end = float(segments[end_idx].get("end") or 0.0)
        seg_ids = [str(segments[k].get("id")) for k in range(start_idx, end_idx + 1)]
        blocks.append({
            "block_id": f"block-{block_num}",
            "start": block_start,
            "end": block_end,
            "start_segment_index": start_idx,
            "end_segment_index": end_idx,
            "segment_ids": seg_ids,
        })
        block_num += 1
        # Advance: compute the next start index by walking back for overlap.
        if end_idx >= n - 1:
            break
        next_start_idx = end_idx + 1
        if overlap_seconds > 0:
            # Walk back from next_start_idx to find overlap start.
            overlap_start_time = block_end - overlap_seconds
            k = next_start_idx - 1
            while k > start_idx:
                seg_k = segments[k]
                if float(seg_k.get("start") or 0.0) <= overlap_start_time:
                    break
                k -= 1
            # Only overlap if we actually go back.
            if k < next_start_idx - 1 and k > start_idx:
                next_start_idx = k + 1
        if next_start_idx <= start_idx:
            next_start_idx = start_idx + 1
        i = next_start_idx
    return blocks


# ---------------------------------------------------------------------------
# Model output validation
# ---------------------------------------------------------------------------


class ModelOutputError(ConversationMiningError):
    """The model output failed validation."""


def _validate_conversation_raw(
    raw: dict,
    segment_ids: list[str],
) -> dict:
    """Validate a single raw conversation dict from the model output.

    Returns the cleaned conversation dict (with resolved fields) or
    raises :class:`ModelOutputError`.
    """
    if not isinstance(raw, dict):
        raise ModelOutputError("conversation entry is not an object")
    start_seg = raw.get("start_segment_id")
    end_seg = raw.get("end_segment_id")
    if not isinstance(start_seg, str) or not isinstance(end_seg, str):
        raise ModelOutputError("start_segment_id/end_segment_id must be strings")
    if start_seg not in segment_ids:
        raise ModelOutputError(f"unknown start_segment_id: {start_seg}")
    if end_seg not in segment_ids:
        raise ModelOutputError(f"unknown end_segment_id: {end_seg}")
    si = segment_ids.index(start_seg)
    ei = segment_ids.index(end_seg)
    if si > ei:
        raise ModelOutputError("start_segment_id after end_segment_id")

    category = raw.get("category")
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ModelOutputError(f"unknown category: {category!r}")

    title = raw.get("title") or ""
    if not isinstance(title, str):
        raise ModelOutputError("title must be a string")
    title = _sanitize_text(title).strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise ModelOutputError("title too long")

    summary = raw.get("summary") or ""
    if not isinstance(summary, str):
        raise ModelOutputError("summary must be a string")
    summary = _sanitize_text(summary).strip()
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise ModelOutputError("summary too long")

    signals = raw.get("signals") or []
    if not isinstance(signals, list):
        raise ModelOutputError("signals must be a list")
    clean_signals: list[str] = []
    for s in signals:
        if isinstance(s, str) and s in SIGNALS and s not in clean_signals:
            clean_signals.append(s)

    confidence = raw.get("confidence")
    if confidence is None:
        confidence = 0.5
    if not isinstance(confidence, (int, float)):
        raise ModelOutputError("confidence must be a number")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ModelOutputError("confidence out of range [0,1]")

    requires_prev = bool(raw.get("requires_previous_context", False))
    requires_next = bool(raw.get("requires_following_context", False))

    return {
        "start_segment_id": start_seg,
        "end_segment_id": end_seg,
        "title": title,
        "summary": summary,
        "category": category,
        "signals": clean_signals,
        "requires_previous_context": requires_prev,
        "requires_following_context": requires_next,
        "confidence": confidence,
    }


def validate_model_output(
    raw_text: str,
    segment_ids: list[str],
) -> list[dict]:
    """Parse and validate the raw model JSON output.

    Returns a list of validated conversation dicts. Raises
    :class:`ModelOutputError` on validation failure.
    """
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"JSON parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelOutputError("root is not an object")
    conversations = data.get("conversations")
    if conversations is None:
        conversations = []
    if not isinstance(conversations, list):
        raise ModelOutputError("conversations must be a list")
    out: list[dict] = []
    for raw in conversations:
        try:
            out.append(_validate_conversation_raw(raw, segment_ids))
        except ModelOutputError:
            raise
    return out


def attempt_json_repair(raw_text: str) -> str:
    """Perform exactly one controlled JSON repair attempt.

    Handles common LLM output issues:
    - Markdown code fences (```json ... ```)
    - Trailing text after the JSON object
    - Trailing commas before closing brackets/braces
    """
    text = raw_text.strip()
    # Strip markdown code fences.
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first fence line.
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove last fence line.
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # If there's text before the first {, try to find the JSON object.
    if text and not text.startswith("{"):
        idx = text.find("{")
        if idx >= 0:
            text = text[idx:]
    # If there's text after the last }, trim it.
    if text and text.startswith("{"):
        # Find the matching closing brace by counting depth.
        depth = 0
        last_close = -1
        in_string = False
        escape = False
        for idx, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_close = idx
                    break
        if last_close >= 0:
            text = text[: last_close + 1]
    # Remove trailing commas.
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _segment_overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Compute the overlap fraction between two segment index ranges.

    Returns the fraction of the smaller range that is covered by the
    overlap.
    """
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    overlap = max(0, overlap_end - overlap_start + 1)
    a_span = a_end - a_start + 1
    b_span = b_end - b_start + 1
    smaller = min(a_span, b_span)
    if smaller <= 0:
        return 0.0
    return overlap / smaller


def _merge_signals(a: list[str], b: list[str]) -> list[str]:
    """Merge two signal lists, preserving order and removing duplicates."""
    out: list[str] = []
    for s in a + b:
        if s in SIGNALS and s not in out:
            out.append(s)
    return out


def deduplicate_conversations(
    conversations: list[dict],
    segment_ids: list[str],
) -> list[dict]:
    """Deduplicate overlapping conversations from different blocks.

    Two conversations are duplicate candidates if their segment index
    ranges overlap by >= 60%. When duplicates are found, the one with
    the larger span is kept; signals are merged.
    """
    if len(conversations) <= 1:
        return list(conversations)

    # Pre-compute index ranges.
    indexed: list[tuple[int, int, int, dict]] = []
    for idx, conv in enumerate(conversations):
        try:
            si = segment_ids.index(conv["start_segment_id"])
            ei = segment_ids.index(conv["end_segment_id"])
        except (ValueError, KeyError):
            continue
        indexed.append((idx, si, ei, conv))
    if not indexed:
        return list(conversations)

    # Sort by span descending so larger conversations are processed first.
    indexed.sort(key=lambda t: (t[2] - t[1]), reverse=True)

    kept: list[dict] = []
    used = set()
    for idx, si, ei, conv in indexed:
        if idx in used:
            continue
        current = dict(conv)
        for jdx, jsi, jei, other in indexed:
            if jdx == idx or jdx in used:
                continue
            overlap = _segment_overlap_fraction(si, ei, jsi, jei)
            if overlap < DEDUP_OVERLAP_THRESHOLD:
                continue
            # jdx is a duplicate of idx. Merge signals.
            current["signals"] = _merge_signals(
                current.get("signals") or [],
                other.get("signals") or [],
            )
            # Prefer the larger span (current is already larger because
            # we sorted descending, but double-check).
            other_span = jei - jsi
            current_span = ei - si
            if other_span > current_span:
                current["start_segment_id"] = other["start_segment_id"]
                current["end_segment_id"] = other["end_segment_id"]
                current_span = other_span
            used.add(jdx)
        kept.append(current)
        used.add(idx)
    return kept


# ---------------------------------------------------------------------------
# Boundary cleanup
# ---------------------------------------------------------------------------


def cleanup_boundaries(
    conversations: list[dict],
    segments: list[dict],
) -> list[dict]:
    """Expand conversation boundaries to avoid cutting off sentences.

    Only expands within a small configurable neighbourhood (max
    BOUNDARY_EXPAND_SEGMENTS segments or BOUNDARY_EXPAND_SECONDS per
    side). Never goes outside the segment list.
    """
    if not conversations or not segments:
        return list(conversations)
    seg_ids = [str(s.get("id")) for s in segments]
    out: list[dict] = []
    for conv in conversations:
        try:
            si = seg_ids.index(conv["start_segment_id"])
            ei = seg_ids.index(conv["end_segment_id"])
        except (ValueError, KeyError):
            out.append(conv)
            continue
        # Expand start backwards.
        new_si = si
        conv_start_time = float(segments[si].get("start") or 0.0)
        for k in range(si - 1, max(-1, si - 1 - BOUNDARY_EXPAND_SEGMENTS), -1):
            if k < 0:
                break
            seg_k = segments[k]
            seg_k_end = float(seg_k.get("end") or 0.0)
            if conv_start_time - seg_k_end > BOUNDARY_EXPAND_SECONDS:
                break
            # Only expand if the gap (pause) is small.
            pause = conv_start_time - seg_k_end
            if pause > 3.0:
                break
            new_si = k
            conv_start_time = float(seg_k.get("start") or 0.0)
        # Expand end forwards.
        new_ei = ei
        conv_end_time = float(segments[ei].get("end") or 0.0)
        for k in range(ei + 1, min(len(segments), ei + 1 + BOUNDARY_EXPAND_SEGMENTS)):
            seg_k = segments[k]
            seg_k_start = float(seg_k.get("start") or 0.0)
            if seg_k_start - conv_end_time > BOUNDARY_EXPAND_SECONDS:
                break
            pause = seg_k_start - conv_end_time
            if pause > 3.0:
                break
            new_ei = k
            conv_end_time = float(seg_k.get("end") or 0.0)
        result = dict(conv)
        result["start_segment_id"] = seg_ids[new_si]
        result["end_segment_id"] = seg_ids[new_ei]
        out.append(result)
    return out


# ---------------------------------------------------------------------------
# Conversation finalization (resolve timestamps + excerpt)
# ---------------------------------------------------------------------------


def finalize_conversations(
    conversations: list[dict],
    segments: list[dict],
) -> list[dict]:
    """Resolve timestamps and build transcript excerpts for each conversation.

    Each finalized conversation has::

        {
          "id": "uuid",
          "start": float,
          "end": float,
          "start_segment_id": str,
          "end_segment_id": str,
          "title": str,
          "summary": str,
          "topic": str | None,
          "category": str,
          "transcript_excerpt": str,
          "signals": [str, ...],
          "context": {
            "requires_previous_context": bool,
            "requires_following_context": bool,
          },
          "confidence": float,
        }
    """
    if not conversations or not segments:
        return []
    seg_map: dict[str, dict] = {}
    for seg in segments:
        sid = str(seg.get("id"))
        if sid:
            seg_map[sid] = seg
    out: list[dict] = []
    for conv in conversations:
        start_seg = seg_map.get(conv.get("start_segment_id"))
        end_seg = seg_map.get(conv.get("end_segment_id"))
        if start_seg is None or end_seg is None:
            continue
        start = float(start_seg.get("start") or 0.0)
        end = float(end_seg.get("end") or 0.0)
        if start >= end:
            continue
        # Build excerpt from segments in range.
        seg_ids = [str(s.get("id")) for s in segments]
        try:
            si = seg_ids.index(conv["start_segment_id"])
            ei = seg_ids.index(conv["end_segment_id"])
        except (ValueError, KeyError):
            continue
        excerpt_parts: list[str] = []
        for k in range(si, ei + 1):
            text = segments[k].get("text") or ""
            if text:
                excerpt_parts.append(text)
        excerpt = " ".join(excerpt_parts)
        if len(excerpt) > MAX_TRANSCRIPT_EXCERPT_LENGTH:
            excerpt = excerpt[:MAX_TRANSCRIPT_EXCERPT_LENGTH] + "…"
        has_corrected = any(
            segments[k].get("corrected_text") is not None
            for k in range(si, ei + 1)
        )
        out.append({
            "id": _new_uuid(),
            "start": start,
            "end": end,
            "start_segment_id": conv["start_segment_id"],
            "end_segment_id": conv["end_segment_id"],
            "title": conv.get("title") or "",
            "summary": conv.get("summary") or "",
            "topic": conv.get("topic"),
            "category": conv.get("category") or "OTHER",
            "transcript_excerpt": excerpt,
            "excerpt_has_corrected": has_corrected,
            "signals": conv.get("signals") or [],
            "context": {
                "requires_previous_context": bool(conv.get("requires_previous_context", False)),
                "requires_following_context": bool(conv.get("requires_following_context", False)),
            },
            "confidence": float(conv.get("confidence") or 0.5),
        })
    # Sort chronologically by start time.
    out.sort(key=lambda c: c["start"])
    return out


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------


def is_stale(run: dict, current_revision: int) -> bool:
    """Return True if the run's transcript_revision != current_revision."""
    if run.get("status") not in TERMINAL_MINING_STATUSES:
        return False
    stored = run.get("transcript_revision")
    if stored is None:
        return False
    return int(stored) != int(current_revision)


# ---------------------------------------------------------------------------
# Persistence (ConversationMiningStore)
# ---------------------------------------------------------------------------


class ConversationMiningStore:
    """Persists a single mining run under the transcript artifact dir.

    Layout::

        vods/{vod_id}/artifacts/transcripts/{transcription_id}/
          conversation_mining/
            {run_id}/
              run.json
              blocks.json
              raw_responses.json
              conversations.json
              worker_job.json
              worker.log
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def mining_dir_for_transcript(
        transcription_service: TranscriptionService,
        transcription_id: str,
    ) -> Path:
        """Return the conversation_mining root dir for a transcript."""
        rec = transcription_service.get_transcription(transcription_id)
        vod_id = rec.get("source_id")
        if not vod_id:
            raise ConversationMiningValidationError("transcript record is missing source_id")
        tdir = transcription_service.transcript_dir(
            vod_id, transcription_id, rec.get("source_type")
        )
        return tdir / MINING_SUBDIR

    def run_path(self) -> Path:
        return self.run_dir / RUN_JSON

    def blocks_path(self) -> Path:
        return self.run_dir / BLOCKS_JSON

    def raw_responses_path(self) -> Path:
        return self.run_dir / RAW_RESPONSES_JSON

    def conversations_path(self) -> Path:
        return self.run_dir / CONVERSATIONS_JSON

    def worker_job_path(self) -> Path:
        return self.run_dir / WORKER_JOB_JSON

    def worker_log_path(self) -> Path:
        return self.run_dir / WORKER_LOG

    def save_run(self, run: dict) -> None:
        atomic_write_json(self.run_path(), run, ConversationMiningError, kind="mining-run")

    def load_run(self) -> dict:
        path = self.run_path()
        if not path.is_file():
            raise ConversationMiningNotFoundError(f"mining run not found: {self.run_dir.name}")
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConversationMiningError(f"could not read run.json: {exc}") from exc

    def save_blocks(self, blocks: list[dict]) -> None:
        atomic_write_json(self.blocks_path(), blocks, ConversationMiningError, kind="mining-blocks")

    def load_blocks(self) -> list[dict]:
        path = self.blocks_path()
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save_raw_response(self, block_id: str, raw: str) -> None:
        """Append a raw model response to raw_responses.json (atomically)."""
        path = self.raw_responses_path()
        existing: dict = {}
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    existing = json.load(fh)
                    if not isinstance(existing, dict):
                        existing = {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing[block_id] = raw
        atomic_write_json(path, existing, ConversationMiningError, kind="mining-raw")

    def load_raw_responses(self) -> dict:
        path = self.raw_responses_path()
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_conversations(self, conversations: list[dict]) -> None:
        atomic_write_json(
            self.conversations_path(),
            {"conversations": conversations},
            ConversationMiningError,
            kind="mining-conversations",
        )

    def load_conversations(self) -> list[dict]:
        path = self.conversations_path()
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data.get("conversations") or []
            if isinstance(data, list):
                return data
            return []
        except (OSError, json.JSONDecodeError):
            return []


# ---------------------------------------------------------------------------
# ConversationMiningService
# ---------------------------------------------------------------------------


class ConversationMiningService:
    """Orchestrates conversation mining runs.

    The service creates run records, spawns a worker subprocess per
    run, polls for progress, and handles cancel / retry / recovery.
    The worker subprocess (``conversation_mining_worker``) loads the
    configured HuggingFace text model and processes blocks sequentially.
    """

    def __init__(
        self,
        transcription_service: TranscriptionService,
        gpu_lock: GpuLock,
        settings: Settings,
        worker_python: Optional[str] = None,
    ) -> None:
        self.transcription_service = transcription_service
        self.gpu_lock = gpu_lock
        self.settings = settings
        self._worker_python = worker_python or sys.executable
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}
        self._orchestrator_thread: Optional[threading.Thread] = None
        self._orchestrator_stop = threading.Event()
        self._runtime_cache: Optional[dict] = None
        self._runtime_cache_time: float = 0.0
        self._recover_on_startup()

    # ------------------------------------------------------------------ public
    def runtime_status(self) -> dict:
        """Return the mining model availability status.

        Distinguishes the individual preconditions that must hold before a
        mining run can succeed:

        * ``model_configured`` — a non-empty model id is set;
        * ``dependencies_available`` — transformers + torch are importable
          in this process (the worker re-checks in its own process);
        * ``cuda_available`` — torch sees CUDA (only relevant when device
          starts with ``cuda``);
        * ``model_cached`` — the model repo is present in the local
          HuggingFace cache (so no download is needed at run time);
        * ``download_required`` — inverse of ``model_cached``;
        * ``worker_available`` — the worker module is importable;
        * ``error`` — a concrete failure reason when not available.

        No secrets or absolute cache paths are exposed.
        """
        now = time.time()
        if self._runtime_cache is not None and (now - self._runtime_cache_time) < 10.0:
            return dict(self._runtime_cache)
        model_id = (self.settings.conversation_mining_model_id or "").strip()
        model_configured = bool(model_id)
        deps_ok, dep_reason = self._check_dependencies()
        cuda_available = self._check_cuda_available()
        device = self.settings.conversation_mining_device or "cuda"
        cuda_relevant = device.lower().startswith("cuda")
        model_cached = self._is_model_cached(model_id) if model_configured else False
        worker_available = self._check_worker_module()
        reasons: list[str] = []
        if not model_configured:
            reasons.append("no model configured")
        if not deps_ok:
            reasons.append(dep_reason or "dependencies missing")
        if cuda_relevant and not cuda_available:
            reasons.append("CUDA not available")
        if not worker_available:
            reasons.append("worker module not importable")
        available = (
            model_configured
            and deps_ok
            and worker_available
            and (not cuda_relevant or cuda_available)
        )
        status = {
            "available": available,
            "model_configured": model_configured,
            "dependencies_available": deps_ok,
            "cuda_available": cuda_available,
            "model_cached": model_cached,
            "download_required": model_configured and not model_cached,
            "worker_available": worker_available,
            "model": model_id,
            "device": device,
            "dtype": self.settings.conversation_mining_dtype,
            "thinking_enabled": self.settings.conversation_mining_thinking_enabled,
            "max_input_tokens": self.settings.conversation_mining_max_input_tokens,
            "max_new_tokens": self.settings.conversation_mining_max_new_tokens,
            "busy": self.gpu_lock.is_busy(),
            "busy_owner_type": (self.gpu_lock.current_owner() or {}).get("owner_type"),
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }
        self._runtime_cache = status
        self._runtime_cache_time = now
        return dict(status)

    def preflight(self) -> tuple[bool, list[str]]:
        """Pre-run validation for the pipeline.

        Returns ``(ok, reasons)``. When ``ok`` is False the pipeline must
        not start the expensive upstream steps (download / audio /
        transcription) because mining would fail anyway.
        """
        status = self.runtime_status()
        # Refresh the cache so callers see a fresh result.
        self._runtime_cache = None
        status = self.runtime_status()
        reasons: list[str] = []
        if not status.get("model_configured"):
            reasons.append("conversation mining model is not configured")
        if not status.get("dependencies_available"):
            reasons.append("mining worker dependencies missing (transformers/torch)")
        if not status.get("worker_available"):
            reasons.append("mining worker module not importable")
        if (
            status.get("cuda_available") is False
            and (status.get("device") or "").lower().startswith("cuda")
        ):
            reasons.append("CUDA not available for mining device")
        return (len(reasons) == 0, reasons)

    # ------------------------------------------------------------------ helpers
    def _check_dependencies(self) -> tuple[bool, Optional[str]]:
        """Check transformers + torch importable. Returns (ok, reason)."""
        try:
            import transformers  # noqa: F401
        except ImportError:
            return False, "transformers is not installed (see requirements-gpu.txt)"
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, "torch is not installed (see requirements-gpu.txt)"
        return True, None

    def _check_cuda_available(self) -> bool:
        try:
            import torch  # noqa: F401
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _check_worker_module(self) -> bool:
        try:
            import importlib
            importlib.import_module("ttvturbo.media_processing.conversation_mining_worker")
            return True
        except Exception:
            return False

    def _is_model_cached(self, model_id: str) -> bool:
        """Best-effort check whether the model repo is in the HF cache.

        Returns False when the check cannot be performed (no huggingface_hub,
        no cache dir). Never raises and never exposes the cache path.
        """
        if not model_id:
            return False
        try:
            from huggingface_hub import try_to_load_from_cache  # type: ignore
            # config.json is always present for a real model repo.
            path = try_to_load_from_cache(model_id, "config.json")
            # When the file is not cached, huggingface_hub returns None or
            # raises; a returned path means it is cached.
            return path is not None and not str(path).endswith("None")
        except Exception:
            return False

    def start_run(self, media_item_id: str, force: bool = False) -> dict:
        """Start a new mining run for the given media item (VOD id).

        Validates that a READY transcript exists and loads the
        effective-text contract. Checks idempotency: if a completed run
        for the same transcript_revision + model_id + config_version
        exists, returns it unless ``force`` is True.
        """
        _validate_uuid(media_item_id)
        # Find the READY transcript for this media item.
        transcripts = self.transcription_service.list_transcriptions(vod_id=media_item_id)
        ready = next(
            (t for t in transcripts if t.get("status") == TranscriptionStatus.READY.value),
            None,
        )
        if ready is None:
            raise ConversationMiningValidationError(
                f"no READY transcript found for media item {media_item_id}"
            )
        transcription_id = ready.get("id")
        if not transcription_id:
            raise ConversationMiningValidationError("transcript record has no id")

        # Load the effective-text contract.
        try:
            contract = self.transcription_service.get_transcript_contract(transcription_id)
        except Exception as exc:
            raise ConversationMiningValidationError(
                f"could not load transcript contract: {exc}"
            ) from exc

        effective_text = contract.get("effective_text") or ""
        effective_segments = contract.get("effective_segments") or []
        if not effective_text.strip() or not effective_segments:
            raise ConversationMiningValidationError(
                "effective transcript is empty or has no segments"
            )
        # Validate timestamps.
        for seg in effective_segments:
            start = seg.get("start")
            end = seg.get("end")
            if start is None or end is None:
                raise ConversationMiningValidationError(
                    "transcript segments missing timestamps"
                )

        transcript_revision = int(contract.get("revision") or 1)
        model_id = self.settings.conversation_mining_model_id or ""

        # Idempotency check.
        if not force:
            existing = self._find_existing_run(
                transcription_id, transcript_revision, model_id, MINING_CONFIG_VERSION
            )
            if existing is not None:
                return existing

        # Check model availability.
        if not model_id.strip():
            raise ConversationMiningUnavailableError(
                "conversation mining model is not configured"
            )

        # Check for active run on same transcript.
        for run in self._iter_all_runs():
            if (
                run.get("transcript_id") == transcription_id
                and run.get("status") in ACTIVE_MINING_STATUSES
            ):
                raise ConversationMiningConflictError(
                    "a mining run is already active for this transcript"
                )

        # Build blocks.
        blocks = build_blocks(
            effective_segments,
            target_seconds=self.settings.conversation_mining_block_target_seconds,
            max_seconds=self.settings.conversation_mining_block_max_seconds,
            overlap_seconds=self.settings.conversation_mining_block_overlap_seconds,
            pause_seconds=self.settings.conversation_mining_pause_seconds,
        )
        if not blocks:
            raise ConversationMiningValidationError(
                "could not build any blocks from the transcript segments"
            )

        run_id = _new_uuid()
        now = _now_iso()
        run = {
            "schema_version": SCHEMA_VERSION,
            "id": run_id,
            "media_item_id": media_item_id,
            "transcript_id": transcription_id,
            "transcript_revision": transcript_revision,
            "status": MiningRunStatus.QUEUED,
            "model": {
                "provider": "local",
                "model_id": model_id,
                "revision": None,
            },
            "mining_config_version": MINING_CONFIG_VERSION,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "blocks": [self._block_status(b) for b in blocks],
            "conversations": [],
            "progress": 0.0,
            "current_block": None,
        }
        store = self._store_for(transcription_id, run_id)
        store.save_run(run)
        store.save_blocks(blocks)

        # Write the worker job file.
        worker_job = {
            "run_id": run_id,
            "transcription_id": transcription_id,
            "media_item_id": media_item_id,
            "model_id": model_id,
            "device": self.settings.conversation_mining_device,
            "dtype": self.settings.conversation_mining_dtype,
            "max_new_tokens": self.settings.conversation_mining_max_new_tokens,
            "max_input_tokens": self.settings.conversation_mining_max_input_tokens,
            "thinking_enabled": self.settings.conversation_mining_thinking_enabled,
            "gpu_lock_data_dir": str(self.gpu_lock.data_dir),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
            "run_dir": str(store.run_dir),
            "blocks": blocks,
            "effective_segments": effective_segments,
            "effective_text": effective_text,
            "transcript_revision": transcript_revision,
            "media_title": ready.get("title") or "",
            "twitch_profile": None,
            "game_info": None,
            "model_cache_dir": self.settings.asr_model_cache_dir,
        }
        # Attach media title / profile from the VOD record if available.
        try:
            vod = self.transcription_service.source_resolver.vod_storage.load_vod(media_item_id)
            worker_job["media_title"] = vod.get("title") or worker_job["media_title"]
            worker_job["twitch_profile"] = vod.get("profile_id")
        except Exception:
            pass
        atomic_write_json(
            store.worker_job_path(),
            worker_job,
            ConversationMiningError,
            kind="mining-worker-job",
        )

        # Start the worker subprocess.
        self._start_worker(store, run_id)
        self._ensure_orchestrator()
        return store.load_run()

    def get_run(self, run_id: str) -> dict:
        run, store = self._find_run(run_id)
        # Check stale.
        try:
            contract = self.transcription_service.get_transcript_contract(run["transcript_id"])
            current_rev = int(contract.get("revision") or 1)
            if is_stale(run, current_rev):
                if run["status"] != MiningRunStatus.STALE:
                    run["status"] = MiningRunStatus.STALE
                    store.save_run(run)
        except Exception:
            pass
        conversations = store.load_conversations()
        run["conversations"] = conversations
        return run

    def list_runs(
        self,
        media_item_id: Optional[str] = None,
        transcript_id: Optional[str] = None,
        status: Optional[str] = None,
        stale: Optional[bool] = None,
    ) -> list[dict]:
        runs = list(self._iter_all_runs())
        if media_item_id:
            runs = [r for r in runs if r.get("media_item_id") == media_item_id]
        if transcript_id:
            runs = [r for r in runs if r.get("transcript_id") == transcript_id]
        if status:
            wanted = {s.strip() for s in status.split(",") if s.strip()}
            runs = [r for r in runs if r.get("status") in wanted]
        if stale is True:
            runs = [r for r in runs if r.get("status") == MiningRunStatus.STALE]
        elif stale is False:
            runs = [r for r in runs if r.get("status") != MiningRunStatus.STALE]
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        # Attach conversations.
        for run in runs:
            try:
                _, store = self._find_run(run["id"])
                run["conversations"] = store.load_conversations()
            except Exception:
                run["conversations"] = []
        return runs

    def cancel_run(self, run_id: str) -> dict:
        run, store = self._find_run(run_id)
        if run["status"] not in CANCELLABLE_MINING_STATUSES:
            raise ConversationMiningConflictError(
                f"run can only be canceled while active (current: {run['status']})"
            )
        # Terminate the worker subprocess.
        with self._lock:
            proc = self._active.pop(run_id, None)
        if proc is not None and proc.poll() is None:
            from ttvturbo.lifecycle import terminate_subprocess
            terminate_subprocess(proc, label=f"mining-worker-{run_id}")
        run = store.load_run()
        run["status"] = MiningRunStatus.CANCELED
        run["completed_at"] = _now_iso()
        # Mark unfinished blocks as CANCELED.
        for block in run.get("blocks") or []:
            if block.get("status") not in ("COMPLETED", "FAILED"):
                block["status"] = "CANCELED"
        run["progress"] = self._compute_progress(run)
        store.save_run(run)
        return run

    def retry_run(self, run_id: str) -> dict:
        run, store = self._find_run(run_id)
        if run["status"] in ACTIVE_MINING_STATUSES:
            raise ConversationMiningConflictError(
                "retry is only allowed for terminal runs"
            )
        if run["status"] not in (
            MiningRunStatus.FAILED,
            MiningRunStatus.CANCELED,
            MiningRunStatus.STALE,
        ):
            raise ConversationMiningConflictError(
                "retry is only allowed for FAILED, CANCELED or STALE runs"
            )
        # Reset failed/canceled blocks.
        blocks = run.get("blocks") or []
        for block in blocks:
            st = block.get("status")
            if st in ("FAILED", "CANCELED", "PENDING", "QUEUED"):
                block["attempt"] = int(block.get("attempt") or 0) + (1 if st == "FAILED" else 0)
                block["status"] = "QUEUED"
                block["error"] = None
                block["result_count"] = None
        run["blocks"] = blocks
        run["status"] = MiningRunStatus.QUEUED
        run["error"] = None
        run["completed_at"] = None
        run["started_at"] = None
        run["progress"] = 0.0
        store.save_run(run)
        # Re-read the worker job and restart.
        self._start_worker(store, run_id)
        self._ensure_orchestrator()
        return store.load_run()

    def delete_run(self, run_id: str) -> bool:
        run, store = self._find_run(run_id)
        if run["status"] in ACTIVE_MINING_STATUSES:
            raise ConversationMiningConflictError(
                "cannot delete an active run; cancel it first"
            )
        # Remove the run directory.
        try:
            import shutil
            shutil.rmtree(store.run_dir, ignore_errors=True)
        except Exception:
            pass
        return True

    def get_latest_for_transcript(self, transcription_id: str) -> Optional[dict]:
        """Return the latest terminal run for a transcript, or None."""
        runs = self.list_runs(transcript_id=transcription_id)
        if not runs:
            return None
        # Prefer COMPLETED, then STALE, then others.
        for status_pref in (
            MiningRunStatus.COMPLETED,
            MiningRunStatus.STALE,
            MiningRunStatus.FAILED,
            MiningRunStatus.CANCELED,
        ):
            for r in runs:
                if r.get("status") == status_pref:
                    return r
        return runs[0]

    def shutdown(self) -> None:
        """Stop the orchestrator and terminate active workers."""
        self._orchestrator_stop.set()
        t = self._orchestrator_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        with self._lock:
            items = list(self._active.items())
            self._active.clear()
        from ttvturbo.lifecycle import terminate_subprocess
        for run_id, proc in items:
            terminate_subprocess(proc, label=f"mining-worker-{run_id}")

    # ------------------------------------------------------------------ internal

    def _store_for(self, transcription_id: str, run_id: str) -> ConversationMiningStore:
        mining_root = ConversationMiningStore.mining_dir_for_transcript(
            self.transcription_service, transcription_id
        )
        return ConversationMiningStore(mining_root / run_id)

    def _find_run(self, run_id: str) -> tuple[dict, ConversationMiningStore]:
        _validate_uuid(run_id)
        for run in self._iter_all_runs():
            if run.get("id") == run_id:
                store = self._store_for(run["transcript_id"], run_id)
                return run, store
        raise ConversationMiningNotFoundError(f"mining run not found: {run_id}")

    def _iter_all_runs(self) -> Iterator[dict]:
        """Iterate all mining run records across all transcripts."""
        # Walk all transcript dirs that have a conversation_mining subdir.
        try:
            all_transcripts = self.transcription_service.list_transcriptions()
        except Exception:
            return
        for tmeta in all_transcripts:
            tid = tmeta.get("id")
            if not tid:
                continue
            try:
                mining_root = ConversationMiningStore.mining_dir_for_transcript(
                    self.transcription_service, tid
                )
            except Exception:
                continue
            if not mining_root.is_dir():
                continue
            for sub in mining_root.iterdir():
                if not sub.is_dir():
                    continue
                run_json = sub / RUN_JSON
                if not run_json.is_file():
                    continue
                try:
                    with open(run_json, "r", encoding="utf-8-sig") as fh:
                        run = json.load(fh)
                    if isinstance(run, dict):
                        yield run
                except (OSError, json.JSONDecodeError):
                    continue

    def _find_existing_run(
        self,
        transcription_id: str,
        transcript_revision: int,
        model_id: str,
        config_version: int,
    ) -> Optional[dict]:
        for run in self._iter_all_runs():
            if (
                run.get("transcript_id") == transcription_id
                and int(run.get("transcript_revision") or 0) == transcript_revision
                and (run.get("model") or {}).get("model_id") == model_id
                and int(run.get("mining_config_version") or 0) == config_version
                and run.get("status") == MiningRunStatus.COMPLETED
            ):
                store = self._store_for(transcription_id, run["id"])
                run["conversations"] = store.load_conversations()
                return run
        return None

    def _block_status(self, block: dict) -> dict:
        return {
            "block_id": block["block_id"],
            "start": block["start"],
            "end": block["end"],
            "status": "QUEUED",
            "attempt": 0,
            "model_input_segments": len(block.get("segment_ids") or []),
            "result_count": None,
            "error": None,
        }

    def _start_worker(self, store: ConversationMiningStore, run_id: str) -> None:
        """Spawn the worker subprocess for a run."""
        cmd = [
            self._worker_python,
            "-m",
            "ttvturbo.media_processing.conversation_mining_worker",
            str(store.worker_job_path()),
        ]
        log_fh = open(store.worker_log_path(), "ab")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            log_fh.close()
            raise ConversationMiningError(f"could not start worker: {exc}") from exc
        with self._lock:
            self._active[run_id] = proc
        # Close the file handle in the parent; the child has its own copy.
        try:
            log_fh.close()
        except Exception:
            pass

    def _ensure_orchestrator(self) -> None:
        with self._lock:
            if self._orchestrator_thread is not None and self._orchestrator_thread.is_alive():
                return
            self._orchestrator_stop.clear()
            t = threading.Thread(
                target=self._orchestrator_loop,
                daemon=True,
                name="mining-orchestrator",
            )
            self._orchestrator_thread = t
            t.start()

    def _orchestrator_loop(self) -> None:
        while not self._orchestrator_stop.is_set():
            try:
                self._advance_runs()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("mining orchestrator iteration failed: %s", exc)
            active = any(
                r.get("status") in ACTIVE_MINING_STATUSES
                for r in self._iter_all_runs()
            )
            if not active:
                self._orchestrator_stop.set()
                break
            time.sleep(ORCHESTRATOR_POLL_SECONDS)

    def _advance_runs(self) -> None:
        for run in list(self._iter_all_runs()):
            if run.get("status") not in ACTIVE_MINING_STATUSES:
                continue
            try:
                self._advance_run(run)
            except Exception as exc:
                logger.warning("advance mining run %s failed: %s", run.get("id"), exc)

    def _advance_run(self, run: dict) -> None:
        run_id = run["id"]
        store = self._store_for(run["transcript_id"], run_id)
        # Reload from disk to get the latest state written by the worker.
        try:
            run = store.load_run()
        except ConversationMiningNotFoundError:
            return
        # Check if the worker process is still alive.
        with self._lock:
            proc = self._active.get(run_id)
        worker_alive = proc is not None and proc.poll() is None
        # Update run from block statuses.
        blocks = run.get("blocks") or []
        all_blocks_done = all(
            b.get("status") in ("COMPLETED", "FAILED", "CANCELED") for b in blocks
        )
        any_failed = any(b.get("status") == "FAILED" for b in blocks)
        if run["status"] == MiningRunStatus.QUEUED and any(
            b.get("status") == "RUNNING" for b in blocks
        ):
            run["status"] = MiningRunStatus.RUNNING
            if not run.get("started_at"):
                run["started_at"] = _now_iso()
        if all_blocks_done:
            if any_failed and not worker_alive:
                run["status"] = MiningRunStatus.FAILED
                run["error"] = "one or more blocks failed"
                run["completed_at"] = _now_iso()
            elif not any_failed:
                # All blocks completed — finalize.
                self._finalize_run(run, store)
                run["status"] = MiningRunStatus.COMPLETED
                run["completed_at"] = _now_iso()
            # Clean up the process reference.
            with self._lock:
                self._active.pop(run_id, None)
        elif not worker_alive and run["status"] == MiningRunStatus.RUNNING:
            # Worker died without finishing all blocks.
            # Mark unfinished blocks as FAILED.
            for block in blocks:
                if block.get("status") not in ("COMPLETED", "FAILED", "CANCELED"):
                    block["status"] = "FAILED"
                    block["error"] = "worker process exited unexpectedly"
            run["status"] = MiningRunStatus.FAILED
            run["error"] = "worker process exited unexpectedly"
            run["completed_at"] = _now_iso()
            with self._lock:
                self._active.pop(run_id, None)
        run["progress"] = self._compute_progress(run)
        run["current_block"] = self._current_block(blocks)
        store.save_run(run)

    def _finalize_run(self, run: dict, store: ConversationMiningStore) -> None:
        """Deduplicate, cleanup boundaries and finalize conversations."""
        blocks = store.load_blocks()
        segment_ids: list[str] = []
        for block in blocks:
            segment_ids.extend(block.get("segment_ids") or [])
        # Collect validated conversations from block results.
        all_conversations: list[dict] = []
        for block in blocks:
            result = block.get("result") or {}
            conversations = result.get("conversations") or []
            for conv in conversations:
                all_conversations.append(conv)
        # Get the full segment list from the worker job.
        try:
            with open(store.worker_job_path(), "r", encoding="utf-8-sig") as fh:
                worker_job = json.load(fh)
            segments = worker_job.get("effective_segments") or []
        except Exception:
            segments = []
        full_seg_ids = [str(s.get("id")) for s in segments]
        # Deduplicate.
        deduped = deduplicate_conversations(all_conversations, full_seg_ids)
        # Boundary cleanup.
        cleaned = cleanup_boundaries(deduped, segments)
        # Finalize (resolve timestamps + excerpts).
        finalized = finalize_conversations(cleaned, segments)
        store.save_conversations(finalized)
        run["conversations"] = finalized

    def _compute_progress(self, run: dict) -> float:
        blocks = run.get("blocks") or []
        if not blocks:
            return 0.0
        done = sum(
            1 for b in blocks
            if b.get("status") in ("COMPLETED", "FAILED", "CANCELED")
        )
        return round(done / len(blocks) * 100.0, 1)

    def _current_block(self, blocks: list[dict]) -> Optional[str]:
        for b in blocks:
            if b.get("status") == "RUNNING":
                return b.get("block_id")
        return None

    def _recover_on_startup(self) -> None:
        """Recover active runs after a server restart.

        Any run left in QUEUED or RUNNING is reconciled: if the worker
        process is gone, unfinished blocks are marked FAILED.
        """
        for run in list(self._iter_all_runs()):
            if run.get("status") not in ACTIVE_MINING_STATUSES:
                continue
            run_id = run["id"]
            # The worker process is gone (fresh start). Mark unfinished
            # blocks as FAILED so retry can resume.
            store = self._store_for(run["transcript_id"], run_id)
            blocks = run.get("blocks") or []
            changed = False
            for block in blocks:
                if block.get("status") not in ("COMPLETED", "FAILED", "CANCELED"):
                    block["status"] = "FAILED"
                    block["error"] = "server restarted while block was active"
                    changed = True
            if changed:
                run["status"] = MiningRunStatus.FAILED
                run["error"] = "server restarted while run was active"
                run["completed_at"] = _now_iso()
                run["progress"] = self._compute_progress(run)
                store.save_run(run)
