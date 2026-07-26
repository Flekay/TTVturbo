"""CLI entry point for the audio quality analyzer.

Usage:

    python analyze.py input.wav
    python analyze.py input.wav --output result.json
    python analyze.py input.wav --silence-threshold-dbfs -42
    python analyze.py input.wav --pretty

Exit codes:

    0 = analysis successful, GOOD or EXCELLENT
    1 = analysis successful, REVIEW
    2 = analysis successful, REJECT
    3 = technical error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Allow running both as a module and as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from analyzer import AnalysisError, AudioQualityAnalyzer  # noqa: E402
from models import Quality, QualityConfig  # noqa: E402


def _default_output_path(input_path: str) -> str:
    base, _ = os.path.splitext(input_path)
    return base + ".analysis.json"


def _build_config(args: argparse.Namespace) -> QualityConfig:
    cfg = QualityConfig()
    if args.silence_threshold_dbfs is not None:
        cfg.silence_threshold_dbfs = args.silence_threshold_dbfs
    if args.frame_ms is not None:
        cfg.frame_ms = args.frame_ms
    if args.hop_ms is not None:
        cfg.hop_ms = args.hop_ms
    if args.clipping_magnitude is not None:
        cfg.clipping_magnitude = args.clipping_magnitude
    if args.noise_floor_percentile is not None:
        cfg.noise_floor_percentile = args.noise_floor_percentile
    if args.reference_min_seconds is not None:
        cfg.reference_min_seconds = args.reference_min_seconds
    if args.reference_max_seconds is not None:
        cfg.reference_max_seconds = args.reference_max_seconds
    return cfg


def _print_summary(result: Any) -> None:
    t = result.technical
    lv = result.levels
    si = result.silence
    nz = result.noise
    dr = result.dropouts
    ig = result.integrity
    vcr = result.voice_clone_reference

    print("=" * 70)
    print(f"Audio Quality Report: {os.path.basename(t.path)}")
    print("=" * 70)
    print(f"  Quality:           {result.quality.value}")
    print()
    print("Technical metadata (original file):")
    print(f"  sample_rate:       {t.sample_rate} Hz")
    print(f"  channels:          {t.channels}")
    print(f"  frame_count:       {t.frame_count}")
    print(f"  duration_seconds:  {t.duration_seconds:.3f}")
    print(f"  subtype:           {t.subtype}")
    print(f"  format:            {t.format}")
    print()
    print("Levels (mono mixdown):")
    print(f"  peak_dbfs:         {lv.peak_dbfs}")
    print(f"  rms_dbfs:          {lv.rms_dbfs}")
    print(f"  dc_offset:         {lv.dc_offset:.6f}")
    print(f"  clipping_samples:  {lv.clipping_sample_count} "
          f"({lv.clipping_sample_ratio:.3e} ratio)")
    print()
    print("Silence / framing:")
    print(f"  leading_silence_ms:  {si.leading_silence_ms:.1f}")
    print(f"  trailing_silence_ms: {si.trailing_silence_ms:.1f}")
    print(f"  total_silence_ratio: {si.total_silence_ratio:.3f}")
    print(f"  voice_ratio:         {si.voice_ratio:.3f}")
    print(f"  frames total/silent/active: {si.frame_count_total}/{si.frame_count_silent}/{si.frame_count_active}")
    print()
    print("Noise / SNR:")
    print(f"  estimated_noise_floor_dbfs: {nz.estimated_noise_floor_dbfs}")
    print(f"  estimated_snr_db:           {nz.estimated_snr_db}")
    print(f"  active_frames_used:         {nz.active_frames_used}")
    print()
    print("Dropouts (inside active region):")
    print(f"  dropout_count:        {dr.dropout_count}")
    print(f"  dropout_total_ms:     {dr.dropout_total_ms:.1f}")
    print(f"  longest_dropout_ms:   {dr.longest_dropout_ms:.1f}")
    print()
    print("Integrity:")
    print(f"  has_nan:        {ig.has_nan}")
    print(f"  has_infinity:   {ig.has_infinity}")
    print()
    print("Voice clone reference:")
    print(f"  eligible: {vcr.eligible}")
    print(f"  quality:  {vcr.quality.value}")
    if vcr.reasons:
        print("  reasons:")
        for r in vcr.reasons:
            print(f"    - {r}")
    if vcr.warnings:
        print("  warnings:")
        for w in vcr.warnings:
            print(f"    - {w}")
    if result.reasons:
        print()
        print("Reasons:")
        for r in result.reasons:
            print(f"  - {r}")
    if result.warnings:
        print()
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    print("=" * 70)


def _exit_code(quality: Quality) -> int:
    if quality in (Quality.GOOD, Quality.EXCELLENT):
        return 0
    if quality == Quality.REVIEW:
        return 1
    if quality == Quality.REJECT:
        return 2
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a WAV file for voice clone reference quality."
    )
    parser.add_argument("input", help="Path to the input WAV file.")
    parser.add_argument("--output", "-o", help="Path to the output JSON file. "
                        "Defaults to <input>.analysis.json next to the input.")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print the JSON output.")
    parser.add_argument("--silence-threshold-dbfs", type=float, default=None,
                        help="RMS dBFS threshold below which a frame is silent (default -45).")
    parser.add_argument("--frame-ms", type=float, default=None,
                        help="Frame length in ms (default 20).")
    parser.add_argument("--hop-ms", type=float, default=None,
                        help="Hop length in ms (default 10).")
    parser.add_argument("--clipping-magnitude", type=float, default=None,
                        help="Sample magnitude counted as clipping (default 0.999).")
    parser.add_argument("--noise-floor-percentile", type=float, default=None,
                        help="Percentile of quietest non-empty frames used for noise floor (default 10).")
    parser.add_argument("--reference-min-seconds", type=float, default=None,
                        help="Preferred minimum reference duration in seconds (default 5).")
    parser.add_argument("--reference-max-seconds", type=float, default=None,
                        help="Preferred maximum reference duration in seconds (default 12).")
    args = parser.parse_args(argv)

    config = _build_config(args)
    analyzer = AudioQualityAnalyzer(config=config)

    try:
        result = analyzer.analyze(args.input)
    except AnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return 3

    output_path = args.output or _default_output_path(args.input)
    indent = 2 if args.pretty else None
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=indent, ensure_ascii=False)
            if indent is not None:
                fh.write("\n")
    except OSError as exc:
        print(f"ERROR: failed to write {output_path}: {exc}", file=sys.stderr)
        return 3

    _print_summary(result)
    print(f"Wrote {output_path}")
    return _exit_code(result.quality)


if __name__ == "__main__":
    sys.exit(main())
