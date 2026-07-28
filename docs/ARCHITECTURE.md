# TTVturbo Architecture

## Overview

TTVturbo is a Twitch VOD transcription and voice-clone pipeline built as a
single FastAPI application with a React frontend. The backend is an
installable Python package (`ttvturbo/`) with a clear separation between
the HTTP layer, the service layer, and the storage layer.

## Package Layout

```
TTVturbo/
  pyproject.toml              # Build config, entry points, pytest config
  requirements.txt            # Base runtime dependencies
  requirements-gpu.txt        # CUDA-only dependencies (torch, qwen-tts, ...)
  requirements-dev.txt        # Test dependencies
  ttvturbo/                   # Installable backend package
    __init__.py
    app.py                    # Entry point: creates the app, runs uvicorn
    app_factory.py            # create_app(), ServiceContainer, ServiceOverrides
    settings.py               # Settings dataclass, DataPaths, from_env()
    storage_utils.py          # Canonical storage primitives (atomic_write_json, ...)
    api_utils.py              # Shared API helpers (error_response)
    asr_api.py                # ASR preset/benchmark/audio-forensics router
    library_api.py            # Library router (items, uploads, file download)
    media_processing_api.py   # Media-processing router (transcriptions, pipeline, uploads)
    vod_pipeline_api.py       # VOD-pipeline + Twitch-status router
    voice_profiles_api.py     # Voice-profiles router
    migrate_to_library.py     # One-time migration tool (VODs + uploads -> library)
    verify.py                 # Manual E2E verification script
    library/                  # Library core (no FastAPI, no React)
      __init__.py             #   public exports
      schemas.py              #   dataclasses, error types, schema version
      storage.py              #   filesystem-backed item store
      service.py              #   LibraryService (business logic)
    media_processing/         # Media-processing core (no FastAPI)
      __init__.py             #   public exports
      schemas.py              #   dataclasses, error types
      storage.py              #   MediaJobStorage, PipelineRunStorage
      sources.py              #   MediaSourceResolver (source_type, source_id -> file)
      uploads.py              #   UploadStorage (legacy upload store)
      audio_extraction.py     #   AudioExtractionService
      transcription.py        #   TranscriptionService
      pipeline.py             #   PipelineService (audio -> transcribe -> summarize)
      gpu_lock.py             #   GpuLock (exclusive GPU access)
      asr_presets.py          #   ASR preset definitions
      asr_metrics.py          #   WER/CER metrics (jiwer)
      asr_diagnostics.py      #   VAD/hallucination detection
      asr_benchmark.py        #   AsrBenchmarkService
      asr_benchmark_worker.py #   Worker subprocess for benchmark runs
      asr_models.py           #   ASR model family detection
      audio_forensics.py      #   AudioForensicsService
      transcription_worker.py #   Worker subprocess for transcription
      audio_extraction_worker.py  # Worker subprocess for audio extraction
    vod_pipeline/             # VOD-pipeline core (no FastAPI)
      __init__.py             #   public exports
      schemas.py              #   dataclasses, error types, VodStatus
      storage.py              #   VodPipelineStorage (profiles + VODs)
      twitch_client.py        #   ChannelLister (yt-dlp based)
      service.py              #   VodPipelineService (business logic)
      downloader_worker.py    #   Worker subprocess for VOD downloads
    voice_clone/              # Voice-clone core (no FastAPI)
      __init__.py
      schemas.py              #   GenerationStatus, request/response types
      service.py              #   VoiceCloneService (orchestration)
      runtime.py              #   Worker subprocess for generation
      quality.py              #   Audio quality analyzer
      diagnostics.py          #   Runtime diagnostics (CLI)
    voice_profiles/           # Voice-profiles core (no FastAPI)
      __init__.py
      schemas.py              #   VoiceProfile, VoiceScript, error types
      storage.py              #   VoiceProfileStorage
      service.py              #   VoiceProfileService
      library.py              #   ScriptLibrary (script management)
  frontend/                   # React + TypeScript SPA
    src/
      api/                    # API client (fetch wrapper, error handling)
      components/             # Shared UI components (Radix UI based)
      features/               # Feature modules (library, vodPipeline, ...)
      hooks/                  # Shared hooks (useRecorder, ...)
      pages/                  # Route-level page components
      stores/                 # Zustand stores
      types/                  # Shared TypeScript types and Zod schemas
  tests/                      # Python test suite (pytest)
  scripts/                    # Verification and utility scripts
  config/                     # Voice lab configuration (JSON)
  data/                       # Runtime data directory (created on first run)
```

## Layering

The backend follows a strict three-layer architecture:

1. **API Layer** (`*_api.py`): FastAPI routers that map HTTP requests to
   service calls and translate service exceptions to HTTP status codes.
   No business logic lives here.

2. **Service Layer** (`*/service.py`, `*/sources.py`, etc.): Business
   logic that orchestrates storage, external APIs, and worker subprocesses.
   Services never import FastAPI.

3. **Storage Layer** (`*/storage.py`): Filesystem-backed persistence using
   the canonical storage primitives from `storage_utils.py`. All JSON
   writes are atomic (write-to-tmp + os.replace) with retry on Windows.

### Dependency Flow

```
API routers  →  Services  →  Storage
                ↓
              Worker subprocesses (transcription, audio extraction, VOD download, ...)
```

The API layer never touches storage directly — it goes through services.
The only exception is file uploads, where the API layer writes the
uploaded file via `LibraryStorage.write_item_file()` (a public storage
helper) and then calls `service.save_item()` to persist metadata.

## Key Design Decisions

### App Factory Pattern

`app_factory.py` provides `create_app()` which builds the FastAPI
application from a `Settings` instance. This allows tests to create
isolated app instances with different data directories. The
`ServiceContainer` holds all service instances, and `ServiceOverrides`
allows tests to inject stubs.

### Centralized Settings

`settings.py` provides a `Settings` dataclass with `from_env()` that
reads environment variables. All paths are derived from a single
`data_root` directory. No module-level side effects on import.

### Canonical Storage Primitives

`storage_utils.py` is the single implementation of:
- `atomic_write_json()` — atomic JSON writes with Windows retry
- `read_json()` — JSON reads with error mapping
- `read_json_optional()` — non-raising JSON reads (returns None)
- `validate_uuid()` — UUID validation with canonical form check
- `now_iso()` — timezone-aware ISO timestamps without microseconds
- `safe_record_dir()` — traversal-safe directory resolution

All storage modules delegate to these primitives.

### Worker Subprocess Architecture

GPU-intensive work (transcription, voice-clone generation, VOD download,
ASR benchmarks) runs in isolated worker subprocesses spawned via
`sys.executable -m ttvturbo.<module>`. This ensures:
- The FastAPI process stays responsive
- GPU memory is released when a worker exits
- A crash in one worker never leaks state into the next
- The GPU lock is always held by a single process

### Media Source Resolution

`MediaSourceResolver` is the single extension point that maps
`(source_type, source_id)` to a verified media file on disk. It handles:
- `twitch_vod`: Resolves from VOD storage, follows `library_item_id`
  links for VODs promoted to the library.
- `file_upload`: Resolves from the library (preferred) or legacy upload
  storage.

### Error Mapping

All API routers use `api_utils.error_response()` to produce a consistent
JSON error shape: `{"detail": {"code": "...", "message": "..."}}`. Each
router has a `_map_*_error()` function that maps typed service exceptions
to HTTP status codes — no text-fragment sniffing.

## Entry Points

The package defines three console scripts (in `pyproject.toml`):

| Command           | Module                           | Purpose                    |
|-------------------|----------------------------------|----------------------------|
| `ttvturbo`        | `ttvturbo.app:main`              | Start the FastAPI server   |
| `ttvturbo-verify` | `ttvturbo.verify:main`           | Run E2E verification       |
| `ttvturbo-migrate`| `ttvturbo.migrate_to_library:main` | Migrate VODs to library  |

Alternatively, modules can be run directly:
```powershell
python -m ttvturbo.app
python -m ttvturbo.verify
python -m ttvturbo.migrate_to_library --dry-run
python -m ttvturbo.voice_clone.diagnostics
```

## Installation

```powershell
# Base system (dashboard, recordings, voice-clone orchestration)
python -m pip install -e .
python -m pip install -r requirements.txt

# GPU system (adds torch, qwen-tts, faster-whisper)
python -m pip install -r requirements-gpu.txt \
    --extra-index-url https://download.pytorch.org/whl/cu128
```

## Testing

```powershell
# Backend tests
python -m pytest

# Frontend tests
cd frontend && npm test

# Frontend typecheck
cd frontend && npm run typecheck

# Full local verification
scripts/verify_local.ps1
```
