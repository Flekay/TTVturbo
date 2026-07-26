"""CLI entrypoint for the Qwen3-TTS voice-clone spike.

Real pipeline:

    Modell laden
    -> Referenz-WAV validieren
    -> create_voice_clone_prompt()
    -> generate_voice_clone()
    -> Ergebnis als WAV speichern
    -> WAV erneut oeffnen und validieren

No stubs, no fallback voice, no copied reference file. If any model step
fails the process exits non-zero and writes the failure into the metrics
file so it cannot be mistaken for a successful run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import numpy as np
import soundfile as sf

# Allow running both as ``python spikes/qwen_tts/clone.py`` and as a module.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diagnostics as dx  # noqa: E402
from runtime import (  # noqa: E402
    DEVICE_DEFAULT,
    DTYPE_DEFAULT,
    LANGUAGE_DEFAULT,
    MODEL_ID_DEFAULT,
    QwenTTSRuntime,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clone.py",
        description="Qwen3-TTS local voice-clone spike (real model run).",
    )
    p.add_argument("--ref-audio", required=True, help="Path to the reference WAV file")
    p.add_argument("--ref-text", required=True, help="Exact transcript of the reference audio")
    p.add_argument("--text", required=True, help="Target text to synthesize (max 300 chars)")
    p.add_argument(
        "--language",
        default=LANGUAGE_DEFAULT,
        help="Synthesis language (default: German)",
    )
    p.add_argument("--output", required=True, help="Output WAV path")
    p.add_argument("--model-id", default=MODEL_ID_DEFAULT, help="HuggingFace model id")
    p.add_argument("--device", default=DEVICE_DEFAULT, help="Torch device (default: cuda:0)")
    p.add_argument("--dtype", default=DTYPE_DEFAULT, help="Torch dtype (default: bfloat16)")
    p.add_argument(
        "--attention-backend",
        default=None,
        choices=[None, "flash_attention_2", "sdpa"],
        help="Force an attention backend; default auto-probes flash_attention_2 then sdpa",
    )
    p.add_argument(
        "--x-vector-only-mode",
        action="store_true",
        help="Use speaker embedding only (lower quality); default is full ICL clone",
    )
    p.add_argument(
        "--metrics",
        default=None,
        help="Path to write the metrics JSON (default: <output>.metrics.json)",
    )
    p.add_argument(
        "--report",
        default=None,
        help="Path to write a human-readable run report (default: stdout only)",
    )
    return p


def _write_metrics(metrics_path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(metrics_path)) or ".", exist_ok=True)
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _read_ref_duration(ref_audio: str) -> float:
    data, sr = sf.read(ref_audio)
    return float(len(data)) / float(sr)


def _save_wav(wav: np.ndarray, sr: int, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    sf.write(output_path, np.asarray(wav), sr)


def run(args: argparse.Namespace) -> int:
    metrics_path = args.metrics or (args.output + ".metrics.json")
    status = "NOT_EXECUTED"
    failure_reason = ""

    # 1. Input validation BEFORE any model load.
    pre = dx.validate_inputs(
        ref_audio=args.ref_audio,
        ref_text=args.ref_text,
        text=args.text,
        output_path=args.output,
    )
    if not pre.ok:
        _write_metrics(
            metrics_path,
            {
                "model_id": args.model_id,
                "device": args.device,
                "dtype": args.dtype,
                "status": "FAILED_INPUT_VALIDATION",
                "errors": pre.errors,
                "warnings": pre.warnings,
            },
        )
        print("Input validation failed:", file=sys.stderr)
        for e in pre.errors:
            print(f"  - {e}", file=sys.stderr)
        for w in pre.warnings:
            print(f"  ! {w}", file=sys.stderr)
        return 2
    for w in pre.warnings:
        print(f"! warning: {w}", file=sys.stderr)

    runtime = QwenTTSRuntime(
        model_id=args.model_id,
        device=args.device,
        dtype=args.dtype,
        attention_backend=args.attention_backend,
    )
    print(f"attention backend: {runtime.metrics.attention_backend}")
    if runtime.metrics.attention_backend_fallback:
        print(f"  fallback note: {runtime.metrics.attention_backend_fallback}")

    try:
        # 2. Load model.
        print(f"loading model {args.model_id} on {args.device} ({args.dtype}) ...")
        runtime.load()
        print(
            f"  loaded in {runtime.metrics.model_load_seconds:.2f}s "
            f"(revision={runtime.metrics.model_revision})"
        )

        # 3. Read reference duration for the metrics file.
        runtime.metrics.reference_duration_seconds = _read_ref_duration(args.ref_audio)

        # 4. create_voice_clone_prompt
        print("creating voice clone prompt ...")
        prompt = runtime.create_prompt(
            ref_audio=args.ref_audio,
            ref_text=args.ref_text,
            x_vector_only_mode=args.x_vector_only_mode,
        )

        # 5. generate_voice_clone
        print(f"generating ({args.language}) ...")
        wavs, sr = runtime.generate(
            text=args.text,
            language=args.language,
            voice_clone_prompt=prompt,
        )
        if not wavs:
            raise RuntimeError("model returned no audio")
        wav = wavs[0]
        runtime.metrics.sample_rate = int(sr)
        runtime.metrics.output_duration_seconds = float(len(wav)) / float(sr)

        # 6. Save WAV.
        _save_wav(wav, sr, args.output)
        runtime.metrics.output_sha256 = dx.file_sha256(args.output)
        print(f"  wrote {args.output} ({runtime.metrics.output_duration_seconds:.2f}s @ {sr}Hz)")

        # 7. Re-open and validate the produced WAV.
        post = dx.validate_output(
            output_path=args.output,
            ref_audio_path=args.ref_audio,
            expected_sr=sr,
        )
        for w in post.warnings:
            print(f"! warning: {w}", file=sys.stderr)
        if not post.ok:
            status = "FAILED_OUTPUT_VALIDATION"
            failure_reason = "; ".join(post.errors)
            print("Output validation failed:", file=sys.stderr)
            for e in post.errors:
                print(f"  - {e}", file=sys.stderr)
        else:
            status = "REAL_MODEL_VERIFIED"
    except Exception as exc:  # noqa: BLE001 - surface any model failure honestly
        status = "FAILED_ON_HARDWARE"
        failure_reason = f"{type(exc).__name__}: {exc}"
        print(f"model run failed: {failure_reason}", file=sys.stderr)
    finally:
        # 8. Clean teardown + final VRAM measurement.
        runtime.release()
        runtime.finalize_metrics()

    payload = runtime.metrics.to_json_dict()
    payload["status"] = status
    if failure_reason:
        payload["failure_reason"] = failure_reason
    payload["input_warnings"] = pre.warnings
    _write_metrics(metrics_path, payload)
    print(f"metrics written to {metrics_path} (status={status})")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(_render_report(payload))
        print(f"report written to {args.report}")

    if status == "REAL_MODEL_VERIFIED":
        return 0
    if status.startswith("FAILED_INPUT"):
        return 2
    return 1


def _render_report(payload: dict) -> str:
    lines = [
        "Qwen3-TTS voice-clone spike - run report",
        "=" * 48,
        f"status: {payload.get('status')}",
        f"model_id: {payload.get('model_id')}",
        f"model_revision: {payload.get('model_revision')}",
        f"device: {payload.get('device')}",
        f"dtype: {payload.get('dtype')}",
        f"attention_backend: {payload.get('attention_backend')}",
        f"attention_backend_fallback: {payload.get('attention_backend_fallback')}",
        f"reference_duration_seconds: {payload.get('reference_duration_seconds')}",
        f"output_duration_seconds: {payload.get('output_duration_seconds')}",
        f"model_load_seconds: {payload.get('model_load_seconds')}",
        f"prompt_creation_seconds: {payload.get('prompt_creation_seconds')}",
        f"generation_seconds: {payload.get('generation_seconds')}",
        f"peak_vram_bytes: {payload.get('peak_vram_bytes')}",
        f"peak_ram_bytes: {payload.get('peak_ram_bytes')}",
        f"sample_rate: {payload.get('sample_rate')}",
        f"output_sha256: {payload.get('output_sha256')}",
    ]
    if payload.get("failure_reason"):
        lines.append(f"failure_reason: {payload['failure_reason']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
