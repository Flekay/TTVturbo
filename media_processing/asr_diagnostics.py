"""Hallucination and missing-speech diagnostics for ASR benchmark runs.

All flags here are *heuristics*. They never prove that a segment is a
hallucination or that real speech was missed — they only surface
evidence a human should review. The UI must present them as warnings,
not as verdicts.

VAD speech regions are computed with the *same* Silero VAD implementation
faster-whisper uses internally (``faster_whisper.vad.get_speech_timestamps``
+ ``VadOptions``). We do NOT install a second VAD library. When VAD is
disabled for a run, no speech regions are computed and the
``TEXT_OUTSIDE_VAD_REGION`` flag is not emitted (it would be meaningless
without VAD).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Heuristic thresholds. Deliberately conservative so we do not flood the
# UI with false positives; the user can still inspect every segment.
NO_SPEECH_PROB_WARNING = 0.6
LOW_LOGPROB_WARNING = -1.0
HIGH_COMPRESSION_RATIO_WARNING = 2.4
REPEAT_MIN_SEGMENT_TEXT_LEN = 12


@dataclass
class VadDiagnosis:
    audio_duration_seconds: Optional[float]
    duration_after_vad_seconds: Optional[float]
    removed_by_vad_seconds: Optional[float]
    speech_regions: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_duration_seconds": self.audio_duration_seconds,
            "duration_after_vad_seconds": self.duration_after_vad_seconds,
            "removed_by_vad_seconds": self.removed_by_vad_seconds,
            "speech_regions": list(self.speech_regions),
        }


def compute_vad_regions(
    audio_path: str,
    vad_parameters: Optional[dict[str, Any]] = None,
    chunk_length: float = 30.0,
) -> VadDiagnosis:
    """Run the same Silero VAD faster-whisper uses and return speech regions.

    Returns an empty :class:`VadDiagnosis` (with ``audio_duration_seconds``
    set when measurable) if onnxruntime or the VAD model is unavailable —
    the benchmark still completes, just without VAD diagnostics. This is
    documented behaviour, not a silent fallback: the run record stores
    ``vad_diagnosis.computed == False`` in that case.
    """
    diagnosis = VadDiagnosis(
        audio_duration_seconds=None,
        duration_after_vad_seconds=None,
        removed_by_vad_seconds=None,
    )
    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: PLC0415
        import soundfile as sf  # type: ignore[import-not-found]  # noqa: PLC0415
        from faster_whisper.vad import (  # type: ignore[import-not-found]  # noqa: PLC0415
            VadOptions,
            get_speech_timestamps,
        )
    except Exception:
        return diagnosis

    try:
        audio, sr = sf.read(audio_path, dtype="float32")
    except Exception:
        return diagnosis
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    duration = float(len(audio) / sr) if sr else None
    diagnosis.audio_duration_seconds = duration

    # Mirror faster-whisper's VadOptions construction (transcribe.py):
    # default VadOptions uses max_speech_duration_s=chunk_length and
    # min_silence_duration_ms=160; a dict overrides everything except
    # max_speech_duration_s which is forced to chunk_length.
    params = dict(vad_parameters or {})
    params.pop("max_speech_duration_s", None)
    vad_opts = VadOptions(**params, max_speech_duration_s=chunk_length)

    try:
        chunks = get_speech_timestamps(audio, vad_opts)
    except Exception:
        return diagnosis

    regions: list[dict[str, float]] = []
    total_speech = 0.0
    for ch in chunks:
        start = float(ch.get("start", 0)) / float(sr)
        end = float(ch.get("end", 0)) / float(sr)
        if end <= start:
            continue
        regions.append({"start": round(start, 3), "end": round(end, 3)})
        total_speech += end - start
    diagnosis.speech_regions = regions
    diagnosis.duration_after_vad_seconds = round(total_speech, 3) if total_speech else 0.0
    if duration is not None:
        diagnosis.removed_by_vad_seconds = round(max(0.0, duration - total_speech), 3)
    return diagnosis


# ---------------------------------------------------------------------------
# Flagging
# ---------------------------------------------------------------------------


def _overlaps_any_region(seg_start: float, seg_end: float,
                          regions: list[dict[str, float]],
                          tolerance: float = 0.05) -> bool:
    for r in regions:
        rs = float(r.get("start", 0.0))
        re_ = float(r.get("end", 0.0))
        # Overlap if [seg_start, seg_end] intersects [rs, re_].
        if seg_end >= rs - tolerance and seg_start <= re_ + tolerance:
            return True
    return False


def flag_hallucinations(
    segments: list[dict[str, Any]],
    vad_diagnosis: Optional[VadDiagnosis],
    metrics: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return a list of heuristic hallucination flags for the segments."""
    flags: list[dict[str, Any]] = []
    regions = (vad_diagnosis.speech_regions if vad_diagnosis else []) or []
    vad_active = bool(regions) or (vad_diagnosis is not None and vad_diagnosis.audio_duration_seconds is not None)

    seen_texts: dict[str, int] = {}
    for seg in segments or []:
        seg_id = seg.get("id")
        text = (seg.get("text") or "").strip()
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        no_speech = seg.get("no_speech_probability")
        logprob = seg.get("avg_logprob")
        comp = seg.get("compression_ratio")

        # TEXT_OUTSIDE_VAD_REGION: only meaningful when VAD ran.
        if vad_active and regions and text:
            if not _overlaps_any_region(start, end, regions):
                flags.append({
                    "type": "TEXT_OUTSIDE_VAD_REGION",
                    "severity": "warning",
                    "segment_id": seg_id,
                    "message": (
                        "Transkript wurde außerhalb einer erkannten Sprachregion erzeugt."
                    ),
                })

        if no_speech is not None and float(no_speech) >= NO_SPEECH_PROB_WARNING and text:
            flags.append({
                "type": "HIGH_NO_SPEECH_PROBABILITY",
                "severity": "warning",
                "segment_id": seg_id,
                "message": (
                    f"hohe no_speech_probability ({float(no_speech):.2f}) bei vorhandenem Text."
                ),
            })

        if logprob is not None and float(logprob) <= LOW_LOGPROB_WARNING and text:
            flags.append({
                "type": "LOW_LOGPROB",
                "severity": "info",
                "segment_id": seg_id,
                "message": (
                    f"niedriger avg_logprob ({float(logprob):.2f}) — möglicherweise unsicher."
                ),
            })

        if comp is not None and float(comp) >= HIGH_COMPRESSION_RATIO_WARNING and text:
            flags.append({
                "type": "HIGH_COMPRESSION_RATIO",
                "severity": "warning",
                "segment_id": seg_id,
                "message": (
                    f"hohe compression_ratio ({float(comp):.2f}) — mögliche Wiederholung."
                ),
            })

        if text and len(text) >= REPEAT_MIN_SEGMENT_TEXT_LEN:
            count = seen_texts.get(text, 0)
            if count >= 1:
                flags.append({
                    "type": "REPEATED_SEGMENT_TEXT",
                    "severity": "warning",
                    "segment_id": seg_id,
                    "message": "Segmenttext wurde bereits in einem früheren Segment ausgegeben.",
                })
            seen_texts[text] = count + 1

    # Insertions from ground-truth diff are hallucination candidates too.
    if metrics and metrics.get("available"):
        insertions = int(metrics.get("insertions") or 0)
        if insertions > 0:
            flags.append({
                "type": "GROUND_TRUTH_INSERTIONS",
                "severity": "info",
                "segment_id": None,
                "message": (
                    f"{insertions} eingefügte Wörter gegenüber dem Referenztext — "
                    "mögliche Halluzinationen."
                ),
            })
    return flags


def flag_missing_speech(
    segments: list[dict[str, Any]],
    vad_diagnosis: Optional[VadDiagnosis],
    audio_duration: Optional[float],
    metrics: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return heuristic flags for possibly-missed speech."""
    flags: list[dict[str, Any]] = []
    regions = (vad_diagnosis.speech_regions if vad_diagnosis else []) or []

    # With ground truth: deletions are the strongest signal.
    if metrics and metrics.get("available"):
        deletions = int(metrics.get("deletions") or 0)
        if deletions > 0:
            flags.append({
                "type": "GROUND_TRUTH_DELETIONS",
                "severity": "warning",
                "segment_id": None,
                "message": (
                    f"{deletions} fehlende Wörter gegenüber dem Referenztext — "
                    "möglicherweise ausgelassene Sprache."
                ),
            })

    # Without ground truth: VAD speech region with no overlapping transcript.
    if regions:
        for r in regions:
            rs = float(r.get("start", 0.0))
            re_ = float(r.get("end", 0.0))
            if re_ <= rs:
                continue
            covered = False
            for seg in segments or []:
                ss = float(seg.get("start") or 0.0)
                se = float(seg.get("end") or 0.0)
                if se >= rs - 0.05 and ss <= re_ + 0.05 and (seg.get("text") or "").strip():
                    covered = True
                    break
            if not covered:
                flags.append({
                    "type": "SPEECH_REGION_WITHOUT_TRANSCRIPT",
                    "severity": "warning",
                    "segment_id": None,
                    "message": (
                        f"VAD-Sprachregion {rs:.1f}s–{re_:.1f}s ohne überlappendes Transkript."
                    ),
                })

    # Large speech region with empty text.
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        if not text and (end - start) >= 1.0:
            flags.append({
                "type": "EMPTY_SEGMENT_OVER_LONG_REGION",
                "severity": "info",
                "segment_id": seg.get("id"),
                "message": (
                    f"leeres Segment über {end - start:.1f}s — möglicherweise ausgelassene Sprache."
                ),
            })

    # Unusually short hypothesis relative to speech duration (no ground truth).
    if (
        not metrics
        and vad_diagnosis
        and vad_diagnosis.duration_after_vad_seconds
        and audio_duration
    ):
        speech_dur = float(vad_diagnosis.duration_after_vad_seconds)
        total_text = " ".join((s.get("text") or "").strip() for s in segments or []).strip()
        if speech_dur >= 3.0 and len(total_text) < 5:
            flags.append({
                "type": "SHORT_HYPOTHESIS_FOR_SPEECH_DURATION",
                "severity": "warning",
                "segment_id": None,
                "message": (
                    f"Hypothesentext sehr kurz für {speech_dur:.1f}s erkannte Sprache."
                ),
            })
    return flags
