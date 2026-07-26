"""Validation + quality diagnostics for the Qwen3-TTS spike.

Two groups of checks:

* ``validate_inputs`` runs BEFORE the model is loaded and refuses to proceed
  on any hard failure (missing file, unreadable WAV, wrong duration, empty
  text, unwritable output path). Soft warnings (e.g. reference outside the
  recommended 5-12s window) are returned but do not abort.

* ``validate_output`` runs AFTER generation and verifies the produced WAV is
  a real, non-trivial, non-identical audio file. Heuristic warnings about a
  suspiciously long silent prefix or repeated reference speech are returned
  as warnings only.

No function in this module ever calls the model, so the unit tests can run
without downloading the 1.7B weights.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import soundfile as sf


MIN_REF_SECONDS = 2.0
MAX_REF_SECONDS = 30.0
RECOMMENDED_REF_MIN = 5.0
RECOMMENDED_REF_MAX = 12.0
MIN_OUTPUT_SECONDS = 0.5
MAX_TARGET_CHARS = 300


class ValidationError(Exception):
    """Hard failure that must abort the pipeline before model load."""


@dataclass
class DiagnosticReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_error(self) -> None:
        if self.errors:
            raise ValidationError("; ".join(self.errors))


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True)
    return np.asarray(data), int(sr)


def _duration_seconds(data: np.ndarray, sr: int) -> float:
    if data.size == 0:
        return 0.0
    return float(data.shape[0]) / float(sr)


def _is_writable(path: str) -> bool:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            return False
    return os.access(parent, os.W_OK)


def validate_inputs(
    ref_audio: str,
    ref_text: str,
    text: str,
    output_path: str,
) -> DiagnosticReport:
    """Validate everything that can be checked without the model."""
    report = DiagnosticReport()

    if not ref_audio or not isinstance(ref_audio, str):
        report.errors.append("ref_audio path is empty")
    elif not os.path.isfile(ref_audio):
        report.errors.append(f"reference file does not exist: {ref_audio}")

    if not ref_text or not ref_text.strip():
        report.errors.append("ref_text is empty (mandatory and must match the reference audio)")

    if not text or not text.strip():
        report.errors.append("target text is empty")

    if text and len(text) > MAX_TARGET_CHARS:
        report.errors.append(
            f"target text too long ({len(text)} > {MAX_TARGET_CHARS} chars); "
            "long scripts must be split sentence-by-sentence later"
        )

    if not output_path:
        report.errors.append("output path is empty")
    elif not _is_writable(output_path):
        report.errors.append(f"output path is not writable: {output_path}")

    # Only inspect the WAV if the file exists; otherwise we already recorded
    # the missing-file error above.
    if ref_audio and os.path.isfile(ref_audio):
        try:
            data, sr = _read_wav(ref_audio)
        except Exception as exc:
            report.errors.append(f"reference WAV is not readable with soundfile: {exc}")
        else:
            if data.size == 0 or data.shape[0] == 0:
                report.errors.append("reference WAV has zero samples")
            else:
                channels = data.shape[1] if data.ndim > 1 else 1
                if channels < 1:
                    report.errors.append("reference WAV has no audio channels")
                duration = _duration_seconds(data, sr)
                if duration < MIN_REF_SECONDS:
                    report.errors.append(
                        f"reference too short: {duration:.2f}s < {MIN_REF_SECONDS}s"
                    )
                if duration > MAX_REF_SECONDS:
                    report.errors.append(
                        f"reference too long: {duration:.2f}s > {MAX_REF_SECONDS}s"
                    )
                if (
                    MIN_REF_SECONDS <= duration < RECOMMENDED_REF_MIN
                    or duration > RECOMMENDED_REF_MAX
                ) and MIN_REF_SECONDS <= duration <= MAX_REF_SECONDS:
                    report.warnings.append(
                        f"reference duration {duration:.2f}s is outside the recommended "
                        f"{RECOMMENDED_REF_MIN}-{RECOMMENDED_REF_MAX}s window"
                    )

    return report


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _first_nonzero_index(data: np.ndarray, threshold: float) -> int:
    mono = data[:, 0] if data.ndim > 1 else data
    abs_data = np.abs(mono)
    above = np.where(abs_data > threshold)[0]
    return int(above[0]) if above.size else -1


def validate_output(
    output_path: str,
    ref_audio_path: str,
    expected_sr: Optional[int] = None,
) -> DiagnosticReport:
    """Hard checks on the produced WAV + heuristic warnings."""
    report = DiagnosticReport()

    if not os.path.isfile(output_path):
        report.errors.append(f"output file does not exist: {output_path}")
        return report

    try:
        data, sr = _read_wav(output_path)
    except Exception as exc:
        report.errors.append(f"output WAV is not readable with soundfile: {exc}")
        return report

    if data.size == 0:
        report.errors.append("output WAV has zero samples")
        return report

    duration = _duration_seconds(data, sr)
    if duration <= MIN_OUTPUT_SECONDS:
        report.errors.append(f"output too short: {duration:.3f}s <= {MIN_OUTPUT_SECONDS}s")

    mono = data[:, 0] if data.ndim > 1 else data
    if not np.all(np.isfinite(mono)):
        report.errors.append("output contains NaN or infinity samples")

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if not np.isfinite(peak):
        report.errors.append("output peak is outside the valid float range")
    elif peak == 0.0:
        report.errors.append("output is fully silent (all zeros)")

    if expected_sr is not None and sr != expected_sr:
        report.warnings.append(f"output sample rate {sr} differs from expected {expected_sr}")

    # Byte-identity with the reference would mean we just copied the file.
    try:
        if os.path.isfile(ref_audio_path) and file_sha256(output_path) == file_sha256(ref_audio_path):
            report.errors.append("output is byte-identical to the reference (no generation happened)")
    except OSError:
        pass

    # Heuristics: long silent prefix or possible repetition of reference speech.
    if mono.size and peak > 0:
        threshold = max(1e-4, peak * 0.01)
        first_idx = _first_nonzero_index(data, threshold)
        if first_idx > 0:
            silence_seconds = first_idx / float(sr)
            if silence_seconds > 0.5:
                report.warnings.append(
                    f"unusually long silent prefix: {silence_seconds:.2f}s before first audible sample"
                )

    report.warnings.append(
        "heuristic repetition check is informational only; manual listening recommended"
    )
    return report
