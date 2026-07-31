"""Deterministic FFmpeg renderer for immutable EditProject projections."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from ttvturbo.media_capabilities.frame_pipeline import fail, load_worker_job, save_result, update_job
from ttvturbo.media_capabilities.utils import sha256_file, video_metadata

ELEMENT_KINDS = {"VIDEO", "AUDIO", "IMAGE", "TEXT"}


def _seconds(us: int | float) -> float:
    return float(us) / 1_000_000.0


def _atempo_chain(speed: float) -> str:
    factors: list[float] = []
    value = speed
    while value > 2.0:
        factors.append(2.0); value /= 2.0
    while value < 0.5:
        factors.append(0.5); value /= 0.5
    factors.append(value)
    return ",".join(f"atempo={x:.8f}" for x in factors)


def _escape_subtitle_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'")
    return value


def _srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _write_subtitles(projection: dict[str, Any], job_dir: Path) -> Path | None:
    entries: list[tuple[int, int, str]] = []
    tracks = projection.get("tracks") or {}
    for track_id in projection.get("track_order") or []:
        track = tracks.get(track_id) or {}
        if track.get("type") != "CAPTIONS":
            continue
        captions = track.get("captions") or {}
        for cap_id in track.get("caption_order") or list(captions):
            cap = captions.get(cap_id)
            if not cap:
                continue
            text = str(cap.get("text") or "").strip()
            if not text:
                continue
            entries.append((int(cap.get("start_us") or 0), int(cap.get("end_us") or 0), text))
    if not entries:
        return None
    entries.sort()
    path = job_dir / "captions.srt"
    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        lines.extend([str(index), f"{_srt_time(_seconds(start))} --> {_srt_time(_seconds(end))}", text.replace("\n", " "), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _clip_duration(clip: dict[str, Any]) -> float:
    source = max(0.0, _seconds(int(clip["source_end_us"]) - int(clip["source_start_us"])))
    speed = max(0.05, float(clip.get("speed") or 1.0))
    return source / speed


def _timeline_duration(projection: dict[str, Any]) -> float:
    end = 0.0
    for track in (projection.get("tracks") or {}).values():
        for clip in (track.get("clips") or {}).values():
            end = max(end, _seconds(int(clip.get("timeline_start_us") or 0)) + _clip_duration(clip))
        for cap in (track.get("captions") or {}).values():
            end = max(end, _seconds(int(cap.get("end_us") or 0)))
    if end <= 0:
        raise RuntimeError("edit sequence has no timed media or captions")
    return end


def _element_kind(clip: dict[str, Any], track_type: str | None, source: dict[str, Any] | None) -> str:
    explicit = str(clip.get("kind") or "").upper()
    if explicit in ELEMENT_KINDS:
        return explicit
    if clip.get("text"):
        return "TEXT"
    file_type = str((source or {}).get("file_type") or "").lower()
    if file_type == "audio" or track_type == "AUDIO":
        return "AUDIO"
    if file_type == "image":
        return "IMAGE"
    return "VIDEO"


def _fade_durations(clip: dict[str, Any]) -> tuple[float, float]:
    duration = _clip_duration(clip)
    fade_in = 0.0
    fade_out = 0.0
    for effect in clip.get("effects") or []:
        if str(effect.get("type") or "").upper() != "FADE" or effect.get("enabled") is False:
            continue
        value = min(duration, max(0.0, _seconds(int(effect.get("duration_us") or 0))))
        if str(effect.get("anchor") or "START").upper() == "END":
            fade_out = value
        else:
            fade_in = value
    return fade_in, fade_out


def _escape_drawtext(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("\n", r"\n")
    )


def _text_alpha(clip: dict[str, Any], start: float, duration: float, opacity: float) -> str:
    fade_in, fade_out = _fade_durations(clip)
    factors = [f"{opacity:.8f}"]
    if fade_in > 0:
        factors.append(f"min(1,max(0,(t-{start:.8f})/{fade_in:.8f}))")
    if fade_out > 0:
        end = start + duration
        factors.append(f"min(1,max(0,({end:.8f}-t)/{fade_out:.8f}))")
    return "*".join(factors)


def _compile(desc: dict[str, Any], job_dir: Path) -> tuple[list[str], Path, float]:
    projection = desc["projection"]
    settings = desc["settings"]
    output = projection["output_settings"]
    width = int(output["width"]); height = int(output["height"])
    fps_num = int(output.get("fps_numerator") or 30); fps_den = int(output.get("fps_denominator") or 1)
    duration = _timeline_duration(projection)

    cmd = [desc["ffmpeg_path"], "-hide_banner", "-loglevel", "warning", "-y"]
    filters: list[str] = [f"color=c=black:s={width}x{height}:r={fps_num}/{fps_den}:d={duration:.6f}[base0]"]
    tracks = projection.get("tracks") or {}
    source_files = desc["source_files"]
    input_index = 0
    visual_index = 0
    audio_labels: list[str] = []

    for track_id in projection.get("track_order") or list(tracks):
        track = tracks.get(track_id) or {}
        track_type = str(track.get("type") or "")
        for clip_id in track.get("clip_order") or list((track.get("clips") or {}).keys()):
            clip = (track.get("clips") or {}).get(clip_id)
            if not clip:
                continue
            media_id = str(clip.get("source_media_item_id") or "")
            source = source_files.get(media_id) if media_id else None
            kind = _element_kind(clip, track_type, source)
            if kind != "TEXT" and not source:
                raise RuntimeError(f"element {clip_id} references unresolved source {media_id}")

            source_start = _seconds(int(clip["source_start_us"]))
            source_duration = _seconds(int(clip["source_end_us"]) - int(clip["source_start_us"]))
            speed = max(0.05, float(clip.get("speed") or 1.0))
            timeline_start = _seconds(int(clip.get("timeline_start_us") or 0))
            effective_duration = source_duration / speed
            fade_in, fade_out = _fade_durations(clip)
            current_input: int | None = None

            if source is not None:
                current_input = input_index
                if kind == "IMAGE":
                    cmd += ["-loop", "1", "-framerate", f"{fps_num}/{fps_den}", "-t", f"{source_duration:.6f}", "-i", source["path"]]
                else:
                    cmd += ["-ss", f"{source_start:.6f}", "-t", f"{source_duration:.6f}", "-i", source["path"]]
                input_index += 1

            if kind == "TEXT":
                text_data = clip.get("text") or {}
                content = _escape_drawtext(text_data.get("content") or "Text")
                transform = clip.get("transform") or {"x": 0.0, "y": 0.0, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0.0}
                opacity = max(0.0, min(1.0, float(clip.get("opacity") if clip.get("opacity") is not None else 1.0)))
                font_size = max(1.0, float(text_data.get("font_size") or 64.0))
                color = _escape_drawtext(text_data.get("color") or "white")
                background = str(text_data.get("background_color") or "transparent")
                align = str(text_data.get("align") or "center")
                x0 = width * float(transform.get("x") or 0.0)
                box_width = width * float(transform.get("scale_x") or 1.0)
                if align == "right":
                    x_expr = f"{x0 + box_width:.6f}-text_w"
                elif align == "left":
                    x_expr = f"{x0:.6f}"
                else:
                    x_expr = f"{x0 + box_width / 2:.6f}-text_w/2"
                y0 = height * float(transform.get("y") or 0.0)
                box_height = height * float(transform.get("scale_y") or 1.0)
                y_expr = f"{y0 + box_height / 2:.6f}-text_h/2"
                alpha = _text_alpha(clip, timeline_start, effective_duration, opacity)
                draw_options = [
                    f"text='{content}'",
                    "expansion=none",
                    f"x='{x_expr}'",
                    f"y='{y_expr}'",
                    f"fontsize={font_size:.6f}",
                    f"fontcolor='{color}'",
                    f"alpha='{alpha}'",
                    f"enable='between(t,{timeline_start:.8f},{timeline_start + effective_duration:.8f})'",
                ]
                if background.lower() not in {"", "none", "transparent"}:
                    draw_options += ["box=1", f"boxcolor='{_escape_drawtext(background)}'", "boxborderw=12"]
                previous = f"base{visual_index}"
                next_base = f"base{visual_index + 1}"
                filters.append(f"[{previous}]drawtext={':'.join(draw_options)}[{next_base}]")
                visual_index += 1

            elif kind in {"VIDEO", "IMAGE"}:
                assert current_input is not None and source is not None
                if not source.get("has_video"):
                    raise RuntimeError(f"{kind.lower()} element {clip_id} has no visual stream")
                crop = clip.get("crop") or {"x": 0, "y": 0, "width": 1, "height": 1}
                transform = clip.get("transform") or {"x": 0, "y": 0, "scale_x": 1, "scale_y": 1, "rotation": 0}
                opacity = max(0.0, min(1.0, float(clip.get("opacity") if clip.get("opacity") is not None else 1.0)))
                label = f"vclip{visual_index}"
                vf = [
                    f"trim=duration={source_duration:.6f}",
                    f"setpts=(PTS-STARTPTS)/{speed:.8f}",
                    f"crop=iw*{float(crop['width']):.8f}:ih*{float(crop['height']):.8f}:iw*{float(crop['x']):.8f}:ih*{float(crop['y']):.8f}",
                    f"scale=w='max(2,{width}*{float(transform.get('scale_x', 1)):.8f})':h='max(2,{height}*{float(transform.get('scale_y', 1)):.8f})':force_original_aspect_ratio=decrease",
                ]
                rotation = float(transform.get("rotation") or 0.0)
                if abs(rotation) > 1e-6:
                    vf.append(f"rotate={rotation:.8f}*PI/180:ow=rotw(iw):oh=roth(ih):c=none")
                vf += ["format=rgba", f"colorchannelmixer=aa={opacity:.8f}"]
                if fade_in > 0:
                    vf.append(f"fade=t=in:st=0:d={fade_in:.8f}:alpha=1")
                if fade_out > 0:
                    vf.append(f"fade=t=out:st={max(0.0, effective_duration - fade_out):.8f}:d={fade_out:.8f}:alpha=1")
                vf.append(f"setpts=PTS+{timeline_start:.8f}/TB")
                filters.append(f"[{current_input}:v]{','.join(vf)}[{label}]")
                previous = f"base{visual_index}"
                next_base = f"base{visual_index + 1}"
                x = float(transform.get("x") or 0.0); y = float(transform.get("y") or 0.0)
                filters.append(f"[{previous}][{label}]overlay=x='{width}*{x:.8f}':y='{height}*{y:.8f}':eof_action=pass:repeatlast=0[{next_base}]")
                visual_index += 1

            if (
                settings.get("include_audio", True)
                and kind in {"VIDEO", "AUDIO"}
                and source is not None
                and source.get("has_audio")
                and not bool(clip.get("audio_muted"))
            ):
                assert current_input is not None
                gain = max(0.0, float(clip.get("audio_gain") if clip.get("audio_gain") is not None else 1.0))
                delay = max(0, int(round(timeline_start * 1000)))
                alabel = f"aclip{len(audio_labels)}"
                af = [
                    f"atrim=duration={source_duration:.6f}",
                    "asetpts=PTS-STARTPTS",
                    _atempo_chain(speed),
                    f"volume={gain:.8f}",
                ]
                if fade_in > 0:
                    af.append(f"afade=t=in:st=0:d={fade_in:.8f}")
                if fade_out > 0:
                    af.append(f"afade=t=out:st={max(0.0, effective_duration - fade_out):.8f}:d={fade_out:.8f}")
                af.append(f"adelay={delay}|{delay}")
                filters.append(f"[{current_input}:a]{','.join(af)}[{alabel}]")
                audio_labels.append(alabel)

    final_video = f"base{visual_index}"
    subtitle_path = _write_subtitles(projection, job_dir)
    if subtitle_path:
        escaped = _escape_subtitle_path(subtitle_path)
        filters.append(f"[{final_video}]subtitles='{escaped}'[vsub]")
        final_video = "vsub"
    filters.append(f"[{final_video}]fps={fps_num}/{fps_den},format=yuv420p[vout]")

    if audio_labels:
        filters.append("".join(f"[{x}]" for x in audio_labels) + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0,atrim=duration={duration:.6f}[aout]")
    elif settings.get("include_audio", True):
        filters.append(f"anullsrc=r=48000:cl=stereo:d={duration:.6f}[aout]")

    script = job_dir / "filter_complex.txt"
    script.write_text(";\n".join(filters), encoding="utf-8")
    output_path = job_dir / "output.mp4"
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]"]
    if settings.get("include_audio", True):
        cmd += ["-map", "[aout]"]
    codec = settings.get("video_codec") or "libx264"
    audio_codec = settings.get("audio_codec") or "aac"
    cmd += ["-c:v", codec]
    if codec in {"libx264", "libx265"}:
        cmd += ["-preset", settings.get("preset") or ("veryfast" if settings.get("mode") == "PREVIEW" else "medium"), "-crf", str(settings.get("crf") if settings.get("crf") is not None else (28 if settings.get("mode") == "PREVIEW" else 18))]
    else:
        cmd += ["-preset", settings.get("preset") or "p4", "-cq", str(settings.get("crf") if settings.get("crf") is not None else (28 if settings.get("mode") == "PREVIEW" else 19))]
    if settings.get("include_audio", True):
        cmd += ["-c:a", audio_codec, "-b:a", "192k"]
    cmd += ["-t", f"{duration:.6f}", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
    return cmd, output_path, duration



def _run_ffmpeg(cmd: list[str], job_dir: Path, duration: float) -> tuple[int, str]:
    """Run FFmpeg while draining progress without risking a stderr pipe deadlock."""
    stderr_path = job_dir / "ffmpeg.stderr.log"
    with stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_handle:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_handle,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in {"out_time_ms", "out_time_us"}:
                try:
                    out_us = int(value)
                    progress = 8 + 87 * min(1.0, out_us / max(1.0, duration * 1_000_000))
                    update_job(job_dir, progress=progress, stage="render")
                except ValueError:
                    pass
        code = proc.wait()
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    return code, stderr

def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m ttvturbo.rendering.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    try:
        desc = load_worker_job(job_dir)
        for source in (desc.get("source_files") or {}).values():
            path = Path(source["path"])
            if sha256_file(path) != source["sha256"]:
                return fail(job_dir, f"source changed after render job creation: {path.name}", code="SOURCE_CHANGED", retryable=False)
        update_job(job_dir, status="RUNNING", progress=2, stage="compile_render_graph")
        cmd, output, duration = _compile(desc, job_dir)
        (job_dir / "ffmpeg_command.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=2), encoding="utf-8")
        update_job(job_dir, progress=8, stage="render")
        # FFmpeg diagnostics go to a file rather than an unread stderr PIPE.
        # Otherwise a full stderr buffer can block the process at the 95%
        # progress ceiling even though all frames have already been encoded.
        code, stderr = _run_ffmpeg(cmd, job_dir, duration)
        if code != 0:
            return fail(job_dir, f"ffmpeg render failed: {stderr[-4000:]}", code="RENDER_FAILED")
        update_job(job_dir, progress=96, stage="finalize")
        if not output.is_file() or output.stat().st_size <= 0:
            return fail(job_dir, "render output is missing or empty", code="RENDER_EMPTY")
        meta = video_metadata(desc["ffprobe_path"], output)
        result = {
            "success": True,
            "output_file": output.name,
            "width": meta["width"],
            "height": meta["height"],
            "fps": meta["fps"],
            "duration_seconds": meta["duration_seconds"],
            "file_size_bytes": output.stat().st_size,
            "video_codec": desc["settings"].get("video_codec") or "libx264",
            "audio_codec": (desc["settings"].get("audio_codec") or "aac") if desc["settings"].get("include_audio", True) else None,
            "projection_hash": desc["projection"]["projection_hash"],
            "state_hash": desc["projection"]["state_hash"],
            "error": None,
        }
        save_result(job_dir, result)
        update_job(job_dir, status="COMPLETED", progress=100, stage=None)
        return 0
    except Exception as exc:
        traceback.print_exc()
        return fail(job_dir, str(exc), code="RENDER_FAILED")


if __name__ == "__main__":
    raise SystemExit(main())
