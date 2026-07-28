"""Fast runtime diagnostics for the Qwen3-TTS voice-clone stack.

This module NEVER loads the Qwen3-TTS model. It only checks that the
runtime prerequisites for a real CUDA generation are present:

* Python version
* ``qwen_tts`` importable
* ``torch`` importable + version + ``torch.version.cuda``
* ``torch.cuda.is_available()`` + device name + VRAM
* ``soundfile`` functional (round-trip write/read)
* FFmpeg still on PATH
* model config (model id + revision) known
* data directory writable

The result is a plain dict so it can be returned directly from the REST
API and consumed by the frontend without breaking the existing contract
(only NEW keys are added).

Runnable as a module:

    python -m voice_clone.diagnostics
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Any, Optional

from .schemas import DEVICE_DEFAULT, MODEL_ID_DEFAULT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _python_version() -> str:
    major, minor, micro = sys.version_info[:3]
    return f"{major}.{minor}.{micro}"


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _soundfile_ok() -> tuple[bool, str]:
    """Round-trip a tiny WAV through soundfile to confirm the C libs work."""
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # pragma: no cover - import error path
        return False, f"{type(exc).__name__}: {exc}"

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as tmp:
            tmp_path = tmp.name
        try:
            sr = 8000
            data = (np.zeros(sr, dtype=np.float32) + 1e-4)
            sf.write(tmp_path, data, sr)
            read_back, read_sr = sf.read(tmp_path)
            ok = read_sr == sr and read_back.size == sr
            if not ok:
                return False, "soundfile round-trip mismatch"
            return True, ""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _data_dir_writable(path: str) -> bool:
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


# ---------------------------------------------------------------------------
# Core diagnostic
# ---------------------------------------------------------------------------

def diagnose_runtime(
    model_id: str = MODEL_ID_DEFAULT,
    device: str = DEVICE_DEFAULT,
    data_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Run all fast checks and return a structured status dict.

    ``available`` is True ONLY when a real Qwen-CUDA generation can in
    principle be started: torch is importable, has a CUDA build, sees a
    CUDA device, ``qwen_tts`` is importable, soundfile works, FFmpeg is
    present and the data dir is writable. No value is ever hard-coded.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    # --- torch -----------------------------------------------------------
    torch_version: Optional[str] = None
    torch_cuda_version: Optional[str] = None
    cuda_available = False
    device_name: Optional[str] = None
    vram_total_bytes: Optional[int] = None
    vram_free_bytes: Optional[int] = None

    try:
        import torch  # type: ignore

        torch_version = getattr(torch, "__version__", None)
        torch_cuda_version = getattr(torch.version, "cuda", None)
        if torch_cuda_version is None:
            reasons.append(
                "PyTorch was installed without CUDA support "
                "(torch.version.cuda is None). Install the CUDA wheel "
                "from requirements-gpu.txt."
            )
        else:
            try:
                cuda_available = bool(torch.cuda.is_available())
            except Exception as exc:  # pragma: no cover - defensive
                cuda_available = False
                reasons.append(f"torch.cuda.is_available() raised: {exc}")
            if not cuda_available:
                reasons.append(
                    "torch.cuda.is_available() is False. No CUDA-visible GPU."
                )
            else:
                try:
                    idx = 0
                    if device.startswith("cuda:"):
                        try:
                            idx = int(device.split(":", 1)[1])
                        except ValueError:
                            idx = 0
                    device_name = torch.cuda.get_device_name(idx)
                    props = torch.cuda.get_device_properties(idx)
                    vram_total_bytes = int(props.total_memory)
                    vram_free_bytes = int(
                        max(0, props.total_memory - torch.cuda.memory_reserved(idx))
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    warnings.append(
                        f"Could not read CUDA device properties: {exc}"
                    )
    except Exception as exc:
        reasons.append(f"torch is not importable: {type(exc).__name__}: {exc}")

    # --- qwen_tts --------------------------------------------------------
    qwen_tts_importable = False
    try:
        import qwen_tts  # noqa: F401
        qwen_tts_importable = True
    except Exception as exc:
        reasons.append(
            f"qwen_tts is not importable: {type(exc).__name__}: {exc}"
        )

    # --- model cache -----------------------------------------------------
    # Best-effort check of the HuggingFace hub cache for the configured
    # model repo. Failures degrade to False so the rest of the status
    # payload stays intact.
    model_cached = False
    try:
        from huggingface_hub import scan_cache_dir  # type: ignore[import-not-found]

        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == model_id:
                model_cached = True
                break
    except Exception:
        pass

    # --- soundfile -------------------------------------------------------
    sf_ok, sf_err = _soundfile_ok()
    if not sf_ok:
        reasons.append(f"soundfile is not functional: {sf_err}")

    # --- FFmpeg ----------------------------------------------------------
    ffmpeg_ok = _check_ffmpeg()
    if not ffmpeg_ok:
        reasons.append("FFmpeg is not available on PATH.")

    # --- data dir --------------------------------------------------------
    if data_dir is None:
        data_dir = os.path.join(os.getcwd(), "voice_clones")
    data_dir_writable = _data_dir_writable(data_dir)
    if not data_dir_writable:
        # Do NOT include the full path in the reason: it would leak
        # filesystem layout through the public status API.
        reasons.append("Data directory is not writable.")

    # --- model config ----------------------------------------------------
    model_config_present = bool(model_id)
    if not model_config_present:
        reasons.append("Model id is not configured.")

    available = (
        not reasons
        and cuda_available
        and qwen_tts_importable
        and sf_ok
        and ffmpeg_ok
        and data_dir_writable
        and model_config_present
    )

    return {
        "available": available,
        "busy": False,  # filled in by the service layer
        "model_id": model_id,
        "device": device if cuda_available else None,
        "python_version": _python_version(),
        "torch_version": torch_version,
        "torch_cuda_version": torch_cuda_version,
        "cuda_available": cuda_available,
        "device_name": device_name,
        "vram_total_bytes": vram_total_bytes,
        "vram_free_bytes": vram_free_bytes,
        "qwen_tts_importable": qwen_tts_importable,
        "model_cached": model_cached,
        "soundfile_ok": sf_ok,
        "ffmpeg_ok": ffmpeg_ok,
        "data_dir": data_dir,
        "data_dir_writable": data_dir_writable,
        "model_config_present": model_config_present,
        "reasons": reasons,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_bytes(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def _print_report(report: dict[str, Any]) -> int:
    backend_ok = (
        report["soundfile_ok"]
        and report["ffmpeg_ok"]
        and report["data_dir_writable"]
    )
    qwen_ok = report["available"]

    print("=== TTVturbo diagnostics ===")
    print(f"Python version:      {report['python_version']}")
    print(f"Torch version:       {report['torch_version']}")
    print(f"Torch CUDA version:  {report['torch_cuda_version']}")
    print(f"CUDA available:      {report['cuda_available']}")
    print(f"CUDA device:         {report['device_name'] or '-'}")
    print(f"VRAM total:          {_format_bytes(report['vram_total_bytes'])}")
    print(f"VRAM free:           {_format_bytes(report['vram_free_bytes'])}")
    print(f"qwen_tts importable: {report['qwen_tts_importable']}")
    print(f"soundfile ok:        {report['soundfile_ok']}")
    print(f"FFmpeg ok:           {report['ffmpeg_ok']}")
    print(f"Data dir writable:   {report['data_dir_writable']} ({report['data_dir']})")
    print(f"Model id:            {report['model_id']}")
    print()
    print(f"Backend runtime:     {'READY' if backend_ok else 'FAILED'}")
    print(f"Qwen runtime:        {'READY' if qwen_ok else 'UNAVAILABLE'}")

    if report["reasons"]:
        print()
        print("Reasons:")
        for r in report["reasons"]:
            print(f"  - {r}")
    if report["warnings"]:
        print()
        print("Warnings:")
        for w in report["warnings"]:
            print(f"  - {w}")

    return 0 if backend_ok else 1


def main(argv: list[str]) -> int:
    report = diagnose_runtime()
    return _print_report(report)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main(sys.argv[1:]))
