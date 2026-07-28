"""Audio forensics for ASR diagnosis.

Generates diagnostic audio artifacts from a registered media source so
the user can directly compare what the ASR model actually receives
against the original file and alternative channel/downmix variants.

Artifacts are stored under ``audio_diagnostics/{diagnostic_id}/`` inside
the configured data directory::

    audio_diagnostics/
      {diagnostic_id}/
        metadata.json
        original-reference.json
        current-asr-input.flac
        left-channel.flac
        right-channel.flac
        mono-current.flac
        mono-average.flac

``mono-current.flac`` reproduces the exact production extraction path
(FFmpeg ``-ac 1 -ar 16000 -c:a flac``). ``mono-average.flac`` uses an
explicit equal-weight downmix via the ``pan`` filter
(``pan=mono|c0=0.5*c0+0.5*c1``) to avoid FFmpeg's default downmix
coefficients which may differ by channel layout.

No loudness normalisation, no denoising, no dynamic compression.

Audio metrics reuse the existing :func:`voice_clone.quality.analyze_reference`
analyzer (peak dBFS, RMS dBFS, DC offset, clipping, silence). Speech
regions reuse the existing :func:`media_processing.asr_diagnostics.compute_vad_regions`
(Silero VAD, same as faster-whisper). No second contradictory audio
analysis is written.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import shutil as _shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .sources import MediaSourceResolver

logger = logging.getLogger("ttvturbo.media_processing.audio_forensics")

DIAGNOSTICS_SUBDIR = "audio_diagnostics"
METADATA_FILENAME = "metadata.json"
ORIGINAL_REF_FILENAME = "original-reference.json"

# Artifact filenames — fixed, no client-supplied names.
CURRENT_ASR_INPUT = "current-asr-input"
LEFT_CHANNEL = "left-channel"
RIGHT_CHANNEL = "right-channel"
MONO_CURRENT = "mono-current"
MONO_AVERAGE = "mono-average"

# All valid audio variant IDs.
AUDIO_VARIANTS = (
    CURRENT_ASR_INPUT,
    LEFT_CHANNEL,
    RIGHT_CHANNEL,
    MONO_CURRENT,
    MONO_AVERAGE,
)

DIAGNOSTIC_ONLY_VARIANTS = frozenset({LEFT_CHANNEL, RIGHT_CHANNEL, MONO_AVERAGE})


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_ffmpeg() -> str:
    found = _shutil.which("ffmpeg")
    if not found:
        try:
            from app import _find_executable  # type: ignore[import-not-found]
            found = _find_executable("ffmpeg")
        except Exception:
            pass
    if not found:
        raise RuntimeError("ffmpeg not found on PATH")
    return found


def _find_ffprobe() -> str:
    found = _shutil.which("ffprobe")
    if not found:
        try:
            from app import _find_executable  # type: ignore[import-not-found]
            found = _find_executable("ffprobe")
        except Exception:
            pass
    if not found:
        raise RuntimeError("ffprobe not found on PATH")
    return found


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ffprobe: source stream analysis
# ---------------------------------------------------------------------------


def ffprobe_source_streams(path: Path) -> dict[str, Any]:
    """Return all audio streams from the source file plus format info.

    Returns::

        {
            "format": {"duration_seconds": ..., "bit_rate": ...},
            "audio_streams": [
                {"index": 0, "codec": "aac", "channels": 2,
                 "channel_layout": "stereo", "sample_rate": 48000,
                 "bit_rate": ..., "language": "deu", "title": "...",
                 "duration_seconds": ...},
                ...
            ],
            "video_streams": [...],
        }
    """
    ffprobe = _find_ffprobe()
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(f"ffprobe failed: {stderr}")
    payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    fmt = payload.get("format") or {}
    audio_streams: list[dict[str, Any]] = []
    video_streams: list[dict[str, Any]] = []
    for s in payload.get("streams") or []:
        ct = s.get("codec_type")
        if ct == "audio":
            dur = None
            try:
                dur = float(s.get("duration") or fmt.get("duration") or 0)
            except (TypeError, ValueError):
                pass
            tags = s.get("tags") or {}
            audio_streams.append({
                "index": int(s.get("index", 0)),
                "codec": s.get("codec_name") or "unknown",
                "channels": int(s.get("channels") or 0),
                "channel_layout": s.get("channel_layout") or "unknown",
                "sample_rate": int(s.get("sample_rate") or 0),
                "bit_rate": _safe_int(s.get("bit_rate")),
                "language": tags.get("language") or tags.get("LANGUAGE"),
                "title": tags.get("title") or tags.get("TITLE"),
                "duration_seconds": dur,
            })
        elif ct == "video":
            video_streams.append({
                "index": int(s.get("index", 0)),
                "codec": s.get("codec_name") or "unknown",
                "width": int(s.get("width") or 0),
                "height": int(s.get("height") or 0),
                "fps": s.get("r_frame_rate"),
            })
    format_duration = None
    try:
        format_duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        pass
    return {
        "format": {
            "duration_seconds": format_duration,
            "bit_rate": _safe_int(fmt.get("bit_rate")),
            "size_bytes": _safe_int(fmt.get("size")),
        },
        "audio_streams": audio_streams,
        "video_streams": video_streams,
    }


# ---------------------------------------------------------------------------
# FFmpeg: artifact generation
# ---------------------------------------------------------------------------


def _run_ffmpeg(cmd: list[str], label: str) -> None:
    """Run an FFmpeg command, raising on failure."""
    logger.info("audio forensics: %s", label)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise RuntimeError(f"ffmpeg failed for {label}: {stderr}")


def _build_ffmpeg_cmd(
    source: Path, stream_index: Optional[int], out: Path, audio_filter: Optional[str]
) -> list[str]:
    """Build a fixed FFmpeg command for artifact generation.

    All artifacts are mono, 16 kHz, FLAC, lossless, no normalisation,
    no denoising, no speed change. The only variable is the optional
    ``audio_filter`` (``-af``) for channel selection / downmix.

    We use ``-af`` (not ``-filter_complex``) because ``-map`` already
    selects a single audio stream, and ``-af`` applies to that mapped
    stream directly without needing input labels.

    ``stream_index`` is the **absolute** stream index as reported by
    ffprobe (e.g. 1 for the first audio stream in a file with video at
    index 0). We use ``-map 0:{index}`` (absolute) rather than
    ``-map 0:a:{n}`` (relative to audio streams) to avoid ambiguity.
    """
    cmd = [_find_ffmpeg(), "-hide_banner", "-y"]
    if stream_index is not None:
        cmd += ["-i", str(source), "-map", f"0:{stream_index}"]
    else:
        cmd += ["-i", str(source)]
    cmd += ["-vn"]
    if audio_filter:
        cmd += ["-af", audio_filter]
    else:
        cmd += ["-ac", "1"]  # FFmpeg default mono downmix
    cmd += [
        "-ar", "16000", "-c:a", "flac", "-compression_level", "5",
        "-f", "flac", str(out),
    ]
    return cmd


def _ffmpeg_current_asr_input(source: Path, stream_index: Optional[int], out: Path) -> None:
    """Reproduce the exact production extraction: mono, 16 kHz, FLAC."""
    _run_ffmpeg(_build_ffmpeg_cmd(source, stream_index, out, None), "current-asr-input")


def _ffmpeg_left_channel(source: Path, stream_index: Optional[int], out: Path) -> None:
    """Extract only the left channel, mono, 16 kHz, FLAC."""
    _run_ffmpeg(
        _build_ffmpeg_cmd(source, stream_index, out, "pan=mono|c0=c0"),
        "left-channel",
    )


def _ffmpeg_right_channel(source: Path, stream_index: Optional[int], out: Path) -> None:
    """Extract only the right channel, mono, 16 kHz, FLAC."""
    _run_ffmpeg(
        _build_ffmpeg_cmd(source, stream_index, out, "pan=mono|c0=c1"),
        "right-channel",
    )


def _ffmpeg_mono_current(source: Path, stream_index: Optional[int], out: Path) -> None:
    """Same as current-asr-input — FFmpeg default downmix, mono, 16 kHz."""
    _ffmpeg_current_asr_input(source, stream_index, out)


def _ffmpeg_mono_average(source: Path, stream_index: Optional[int], out: Path) -> None:
    """Equal-weight downmix via pan filter: 0.5*L + 0.5*R, mono, 16 kHz."""
    _run_ffmpeg(
        _build_ffmpeg_cmd(source, stream_index, out, "pan=mono|c0=0.5*c0+0.5*c1"),
        "mono-average",
    )


# ---------------------------------------------------------------------------
# Audio metrics — reuse existing analyzers
# ---------------------------------------------------------------------------


def compute_audio_metrics(path: Path) -> dict[str, Any]:
    """Compute technical audio metrics for a FLAC file.

    Reuses :func:`voice_clone.quality.analyze_reference` for peak dBFS,
    RMS dBFS, DC offset, clipping, silence and integrity. Reuses
    :func:`media_processing.asr_diagnostics.compute_vad_regions` for
    speech region detection (Silero VAD, same as faster-whisper).

    Adds file-level info (size, SHA-256, codec via ffprobe) that the
    quality analyzer does not cover.
    """
    result: dict[str, Any] = {
        "file_size_bytes": None,
        "sha256": None,
        "codec": None,
        "sample_rate": None,
        "channels": None,
        "duration_seconds": None,
        "peak_dbfs": None,
        "rms_dbfs": None,
        "dc_offset": None,
        "clipping_ratio": None,
        "silence_ratio": None,
        "speech_regions": [],
        "speech_duration_seconds": None,
        "quality_report": None,
        "warnings": [],
    }

    # File-level info.
    try:
        result["file_size_bytes"] = path.stat().st_size
    except OSError:
        pass
    try:
        result["sha256"] = _sha256(path)
    except OSError:
        pass

    # ffprobe for codec.
    try:
        ffprobe = _find_ffprobe()
        cmd = [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_streams", str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0:
            payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
            streams = payload.get("streams") or []
            ast = next((s for s in streams if s.get("codec_type") == "audio"), None)
            if ast:
                result["codec"] = ast.get("codec_name") or "flac"
                result["sample_rate"] = int(ast.get("sample_rate") or 0) or None
                result["channels"] = int(ast.get("channels") or 0) or None
                try:
                    result["duration_seconds"] = float(ast.get("duration") or 0) or None
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        result["warnings"].append(f"ffprobe unavailable: {type(exc).__name__}")

    # Reuse the existing voice_clone quality analyzer for signal metrics.
    try:
        from voice_clone.quality import analyze_reference, AnalysisError  # type: ignore[import-not-found]
        analysis = analyze_reference(str(path))
        d = analysis.to_dict()
        result["sample_rate"] = result["sample_rate"] or d["technical"]["sample_rate"]
        result["channels"] = result["channels"] or d["technical"]["channels"]
        result["duration_seconds"] = result["duration_seconds"] or d["technical"]["duration_seconds"]
        result["peak_dbfs"] = d["levels"]["peak_dbfs"]
        result["rms_dbfs"] = d["levels"]["rms_dbfs"]
        result["dc_offset"] = d["levels"]["dc_offset"]
        result["clipping_ratio"] = d["levels"]["clipping_sample_ratio"]
        result["silence_ratio"] = d["silence"]["total_silence_ratio"]
        result["quality_report"] = {
            "quality": d["quality"],
            "reasons": d["reasons"],
            "warnings": d["warnings"],
            "noise": d["noise"],
            "dropouts": d["dropouts"],
            "integrity": d["integrity"],
        }
    except AnalysisError as exc:
        result["warnings"].append(f"audio analysis failed: {exc}")
    except Exception as exc:
        result["warnings"].append(
            f"voice_clone.quality unavailable: {type(exc).__name__}: {exc}"
        )

    # Reuse the existing asr_diagnostics VAD for speech regions.
    try:
        from .asr_diagnostics import compute_vad_regions  # type: ignore[import-not-found]
        vad = compute_vad_regions(str(path))
        result["speech_regions"] = vad.speech_regions
        result["speech_duration_seconds"] = vad.duration_after_vad_seconds
    except Exception as exc:
        result["warnings"].append(
            f"VAD unavailable: {type(exc).__name__}: {exc}"
        )

    return result


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AudioForensicsService:
    """Generates and persists audio forensic diagnostic artifacts."""

    def __init__(self, data_dir: Path, source_resolver: MediaSourceResolver) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / DIAGNOSTICS_SUBDIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_resolver = source_resolver

    # ------------------------------------------------------------------ paths
    def _diag_dir(self, diagnostic_id: str) -> Path:
        try:
            uuid.UUID(diagnostic_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"invalid diagnostic id: {diagnostic_id!r}") from exc
        return self.root / diagnostic_id

    def _diag_path(self, diagnostic_id: str) -> Path:
        return self._diag_dir(diagnostic_id) / METADATA_FILENAME

    def artifact_path(self, diagnostic_id: str, variant: str) -> Path:
        if variant not in AUDIO_VARIANTS:
            raise ValueError(f"invalid audio variant: {variant!r}")
        return self._diag_dir(diagnostic_id) / f"{variant}.flac"

    # ------------------------------------------------------------------ list/get
    def list_diagnostics(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return out
        for entry in entries:
            if not entry.is_dir():
                continue
            meta_path = entry / METADATA_FILENAME
            if meta_path.is_file():
                try:
                    with open(meta_path, "r", encoding="utf-8-sig") as fh:
                        out.append(json.load(fh))
                except (OSError, json.JSONDecodeError):
                    pass
        out.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return out

    def get_diagnostic(self, diagnostic_id: str) -> dict[str, Any]:
        path = self._diag_path(diagnostic_id)
        if not path.is_file():
            raise FileNotFoundError(f"diagnostic not found: {diagnostic_id}")
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------ create
    def create_diagnostic(
        self,
        source_type: str,
        source_id: str,
        audio_stream_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Generate all audio forensic artifacts for a source.

        Returns the diagnostic metadata record.
        """
        # Resolve the source.
        resolved = self.source_resolver.resolve(source_type, source_id)
        source_file = Path(resolved.file_path)
        if not source_file.is_file():
            raise FileNotFoundError(f"source file missing: {source_file}")

        # Probe source streams.
        stream_info = ffprobe_source_streams(source_file)
        audio_streams = stream_info.get("audio_streams") or []

        # Validate stream index if provided.
        if audio_stream_id is not None:
            valid_indices = [s["index"] for s in audio_streams]
            if audio_stream_id not in valid_indices:
                raise ValueError(
                    f"invalid audio_stream_id {audio_stream_id}; "
                    f"valid: {valid_indices}"
                )
            stream_index_for_ffmpeg = audio_stream_id
        else:
            # Default: first audio stream (FFmpeg's default behaviour).
            stream_index_for_ffmpeg = audio_streams[0]["index"] if audio_streams else None

        diagnostic_id = _new_uuid()
        diag_dir = self._diag_dir(diagnostic_id)
        diag_dir.mkdir(parents=True, exist_ok=True)

        now = _now_iso()
        # Generate all artifacts.
        artifacts: dict[str, dict[str, Any]] = {}
        variant_generators = {
            CURRENT_ASR_INPUT: _ffmpeg_current_asr_input,
            LEFT_CHANNEL: _ffmpeg_left_channel,
            RIGHT_CHANNEL: _ffmpeg_right_channel,
            MONO_CURRENT: _ffmpeg_mono_current,
            MONO_AVERAGE: _ffmpeg_mono_average,
        }
        for variant, gen_fn in variant_generators.items():
            out_path = diag_dir / f"{variant}.flac"
            try:
                gen_fn(source_file, stream_index_for_ffmpeg, out_path)
                metrics = compute_audio_metrics(out_path)
                artifacts[variant] = {
                    "filename": f"{variant}.flac",
                    "metrics": metrics,
                }
            except Exception as exc:
                artifacts[variant] = {
                    "filename": f"{variant}.flac",
                    "error": f"{type(exc).__name__}: {exc}",
                    "metrics": None,
                }

        # Original reference info.
        original_ref = {
            "source_type": source_type,
            "source_id": source_id,
            "source_file": str(source_file),
            "source_file_name": source_file.name,
            "stream_info": stream_info,
            "selected_audio_stream_id": stream_index_for_ffmpeg,
        }
        _atomic_write_json(diag_dir / ORIGINAL_REF_FILENAME, original_ref)

        # Metadata.
        metadata = {
            "schema_version": 1,
            "id": diagnostic_id,
            "source_type": source_type,
            "source_id": source_id,
            "audio_stream_id": stream_index_for_ffmpeg,
            "audio_streams": audio_streams,
            "video_streams": stream_info.get("video_streams") or [],
            "format": stream_info.get("format") or {},
            "artifacts": artifacts,
            "created_at": now,
        }
        _atomic_write_json(self._diag_path(diagnostic_id), metadata)
        return metadata

    # ------------------------------------------------------------------ delete
    def delete_diagnostic(self, diagnostic_id: str) -> bool:
        diag_dir = self._diag_dir(diagnostic_id)
        if not diag_dir.exists():
            raise FileNotFoundError(f"diagnostic not found: {diagnostic_id}")
        tmp = diag_dir.with_name(diag_dir.name + ".deleting")
        try:
            os.replace(diag_dir, tmp)
        except OSError as exc:
            raise RuntimeError(f"could not delete diagnostic: {exc}") from exc
        _shutil.rmtree(tmp, ignore_errors=True)
        return True
