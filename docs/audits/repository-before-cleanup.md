# Repository Audit — Before Cleanup

**Erstellt:** 2026-07-28  
**Auftrag:** 1 — IST-Audit und unveraenderliche Baseline

---

## 1. Ausgangscommit und Working-Tree-Zustand

- **Branch:** `main` (up to date with `origin/main`)
- **HEAD:** `def2de2227cabef644eb76ac2b83888920e75b4a`
- **Working Tree:** sauber (`nothing to commit, working tree clean`)
- **Letzte Commits:**
  - `def2de2` Rewrite dashboard with modern layout and SVG charts
  - `c62d260` Fix audio forensics map bug, add NeMo model support and hotwords for production transcription
  - `80cd642` Add audio forensics, multi-model ASR adapters, and NVML-based VRAM measurement
  - `0dd47cb` Wire ASR preset into production transcription, fix benchmark start bug and UI styles
  - `0cae610` Add ASR API endpoints and wire into app

---

## 2. Tatsaechlicher Root-Tree

```
TTVturbo/
  .env.example
  .gitignore
  LICENSE
  README.md
  app.py                      # FastAPI-App, Recordings, Voice-Clone, SPA-Fallback
  asr_api.py                  # ASR-Preset/Benchmark/Audio-Forensics Router
  library_api.py              # Library Router
  media_processing_api.py     # Media-Processing Router
  migrate_to_library.py       # Einmalige Migration (VODs -> Library)
  verify.py                   # Manuelle Verifikation (E2E)
  vod_pipeline_api.py         # VOD-Pipeline + Twitch-Status Router
  voice_profiles_api.py       # Voice-Profiles Router
  pyproject.toml
  requirements.txt
  requirements-dev.txt
  requirements-gpu.txt
  config/
    voice_lab/scripts/de-DE/ttvturbo_voice_pack_v1.json
  library/                    # Backend-Kern: persistent video store
    __init__.py
    schemas.py
    service.py
    storage.py
  media_processing/           # Backend-Kern: audio/transcription/pipeline/ASR
    __init__.py
    asr_benchmark.py
    asr_benchmark_worker.py
    asr_diagnostics.py
    asr_metrics.py
    asr_models.py
    asr_presets.py
    audio_extraction.py
    audio_extraction_worker.py
    audio_forensics.py
    gpu_lock.py
    pipeline.py
    schemas.py
    sources.py
    storage.py
    transcription.py
    transcription_worker.py
    uploads.py
  vod_pipeline/               # Backend-Kern: Twitch/VOD-Download
    __init__.py
    downloader_worker.py
    schemas.py
    service.py
    storage.py
    twitch_client.py
  voice_clone/                # Backend-Kern: Qwen3-TTS Voice-Clone
    __init__.py
    diagnostics.py
    quality.py
    runtime.py
    schemas.py
    service.py
  voice_profiles/             # Backend-Kern: Voice-Profile-Verwaltung
    __init__.py
    library.py
    schemas.py
    service.py
    storage.py
  scripts/
    validate_voice_scripts.py
    verify_local.ps1
  tests/                      # 446 Python-Tests
    conftest.py
    test_asr_api.py
    test_asr_benchmark.py
    test_asr_diagnostics.py
    test_asr_metrics.py
    test_asr_models.py
    test_asr_presets.py
    test_audio_forensics.py
    test_backend_hardening.py
    test_library.py
    test_media_processing.py
    test_recordings.py
    test_spa.py
    test_status.py
    test_transcription_preset.py
    test_vod_api.py
    test_vod_app_integration.py
    test_vod_downloads.py
    test_vod_pipeline.py
    test_voice_clone.py
    test_voice_profile_library.py
    test_voice_profile_service.py
    test_voice_profile_storage.py
    test_voice_profiles_api.py
    test_voice_script_pack.py
  frontend/                   # React SPA (Vite + TypeScript + Vitest)
    index.html
    package.json
    package-lock.json
    tsconfig.json
    vite.config.ts
    vitest.config.ts
    src/
      App.tsx
      main.tsx
      router.tsx
      router.routes.tsx
      api/ (client.ts, recordings.ts, status.ts, voiceClone.ts)
      components/ (layout, ui, recordings, voiceClone)
      features/ (asrComparison, library, mediaProcessing, vodPipeline, voiceProfiles)
      hooks/ (useBackendStatus, useQueries, useRecorder, useVoiceClone)
      pages/ (Dashboard, Library, LibraryDetail, NotFound, Settings, Transcription, TwitchProfiles, Unavailable, VodDetail, VodDownloader, VodPipeline, VoiceClone, VoiceProfiles)
      stores/ (statusHistoryStore, uiStore)
      styles/ (components, dashboard, global, layout, variables)
      test/ (App, AsrComparison, FrontendHardening, MediaProcessing, UnavailablePage, VodPipeline, VoiceClone, VoiceProfiles)
      types/ (recording, schemas, status, voiceClone)
      utils/ (format)
```

---

## 3. Pythonmodule mit Zeilenzahlen

| Modul                                  | Zeilen |
|----------------------------------------|--------|
| vod_pipeline/service.py                | 1135   |
| app.py                                 | 962    |
| voice_clone/service.py                 | 924    |
| media_processing/transcription.py      | 893    |
| media_processing/transcription_worker  | 676    |
| tests/test_backend_hardening.py        | 646    |
| tests/test_media_processing.py         | 629    |
| tests/test_voice_clone.py              | 627    |
| media_processing/asr_models.py         | 580    |
| media_processing_api.py                | 547    |
| voice_clone/quality.py                 | 540    |
| tests/test_voice_profile_service.py    | 520    |
| media_processing/asr_benchmark_worker  | 497    |
| media_processing/asr_benchmark.py      | 491    |
| media_processing/audio_forensics.py    | 488    |
| vod_pipeline_api.py                    | 481    |
| media_processing/audio_extraction.py   | 464    |
| voice_clone/runtime.py                 | 459    |
| tests/test_vod_pipeline.py             | 435    |
| media_processing/pipeline.py           | 429    |
| voice_profiles/service.py              | 404    |
| media_processing/asr_presets.py        | 402    |
| tests/test_vod_downloads.py            | 399    |
| media_processing/audio_extraction_worker| 363  |
| vod_pipeline/downloader_worker.py      | 342    |
| tests/test_audio_forensics.py          | 329    |
| media_processing/gpu_lock.py           | 310    |
| tests/test_voice_profiles_api.py       | 298    |
| vod_pipeline/storage.py                | 295    |
| tests/test_asr_models.py               | 285    |
| voice_profiles_api.py                  | 270    |
| asr_api.py                             | 269    |
| media_processing/asr_diagnostics.py    | 264    |
| voice_clone/diagnostics.py             | 254    |
| media_processing/storage.py            | 248    |
| media_processing/sources.py            | 247    |
| verify.py                              | 245    |
| vod_pipeline/twitch_client.py          | 242    |
| tests/test_vod_api.py                  | 227    |
| tests/conftest.py                      | 222    |
| tests/test_asr_benchmark.py            | 218    |
| library/service.py                     | 216    |
| media_processing/schemas.py            | 216    |
| library/storage.py                     | 204    |
| media_processing/asr_metrics.py        | 201    |
| tests/test_asr_api.py                  | 198    |
| tests/test_library.py                  | 194    |
| tests/test_transcription_preset.py     | 193    |
| media_processing/__init__.py           | 193    |
| voice_profiles/storage.py              | 182    |
| tests/test_voice_profile_storage.py    | 171    |
| vod_pipeline/schemas.py                | 170    |
| migrate_to_library.py                  | 154    |
| tests/test_voice_profile_library.py    | 147    |
| tests/test_asr_presets.py              | 138    |
| voice_profiles/schemas.py              | 124    |
| media_processing/uploads.py            | 118    |
| tests/test_recordings.py               | 118    |
| voice_clone/schemas.py                 | 105    |
| tests/test_asr_metrics.py              | 103    |
| voice_profiles/library.py              | 101    |
| library_api.py                         | 99     |
| tests/test_voice_script_pack.py        | 92     |
| tests/test_status.py                   | 84     |
| tests/test_asr_diagnostics.py          | 76     |
| tests/test_spa.py                      | 68     |
| vod_pipeline/__init__.py               | 66     |
| voice_profiles/__init__.py             | 56     |
| tests/test_vod_app_integration.py      | 53     |
| library/schemas.py                     | 47     |
| library/__init__.py                    | 29     |
| voice_clone/__init__.py                | 5      |

**Summe:** 68 Python-Dateien, ca. 21.500 Zeilen.

---

## 4. Oeffentliche API-Routen

### app.py (direkt auf `app`)

| Methode  | Pfad                                         | Request-Modell      | Response-Modell   |
|----------|----------------------------------------------|---------------------|-------------------|
| GET      | `/`                                          | -                   | FileResponse (SPA)|
| GET      | `/api/status`                                | -                   | JSONResponse      |
| POST     | `/api/recordings`                            | UploadFile          | JSONResponse      |
| GET      | `/api/recordings`                            | -                   | JSONResponse      |
| GET      | `/api/recordings/{filename}`                 | -                   | FileResponse      |
| DELETE   | `/api/recordings/{filename}`                 | -                   | JSONResponse      |
| GET      | `/api/voice-clone/status`                    | -                   | JSONResponse      |
| POST     | `/api/voice-clone/preload-model`             | -                   | JSONResponse      |
| GET      | `/api/voice-clone/analyze-reference/{name}`  | -                   | JSONResponse      |
| POST     | `/api/voice-clone/generations`               | CreateGenerationReq | JSONResponse      |
| GET      | `/api/voice-clone/generations`               | -                   | JSONResponse      |
| GET      | `/api/voice-clone/generations/{id}`          | -                   | JSONResponse      |
| GET      | `/api/voice-clone/generations/{id}/audio`    | -                   | FileResponse      |
| DELETE   | `/api/voice-clone/generations/{id}`          | -                   | JSONResponse      |
| GET      | `/api/voice-clone/generations/{id}/log`      | -                   | JSONResponse      |
| GET/HEAD | `/{full_path:path}` (SPA-Fallback)           | -                   | FileResponse      |

### voice_profiles_api (Prefix `/api/voice-profiles`)

| Methode  | Pfad                                         | Request-Modell        | Response-Modell   |
|----------|----------------------------------------------|-----------------------|-------------------|
| GET      | `/scripts`                                   | -                     | JSONResponse      |
| GET      | `` (root)                                    | -                     | JSONResponse      |
| POST     | `` (root)                                    | CreateProfileRequest  | JSONResponse      |
| GET      | `/{profile_id}`                              | -                     | JSONResponse      |
| PATCH    | `/{profile_id}`                              | PatchProfileRequest   | JSONResponse      |
| DELETE   | `/{profile_id}`                              | -                     | JSONResponse      |
| PUT      | `/{profile_id}/references/{script_id}`       | AttachReferenceReq    | JSONResponse      |
| DELETE   | `/{profile_id}/references/{script_id}`       | -                     | JSONResponse      |
| POST     | `/{profile_id}/references/{script_id}/accept-review` | -            | JSONResponse      |

### vod_pipeline_api (Prefix `/api`)

| Methode  | Pfad                                         | Request-Modell        | Response-Modell   |
|----------|----------------------------------------------|-----------------------|-------------------|
| GET      | `/twitch/profiles`                           | -                     | JSONResponse      |
| POST     | `/twitch/profiles`                           | CreateProfileRequest  | JSONResponse      |
| GET      | `/twitch/profiles/{profile_id}`              | -                     | JSONResponse      |
| POST     | `/twitch/profiles/{profile_id}/refresh`      | -                     | JSONResponse      |
| DELETE   | `/twitch/profiles/{profile_id}`              | -                     | JSONResponse      |
| POST     | `/twitch/profiles/{profile_id}/sync-vods`    | -                     | JSONResponse      |
| GET      | `/vods`                                      | query params          | JSONResponse      |
| GET      | `/vods/{vod_id}`                             | -                     | JSONResponse      |
| POST     | `/vods/import`                               | ImportVodRequest      | JSONResponse      |
| POST     | `/vods/{vod_id}/download`                    | -                     | JSONResponse      |
| POST     | `/vods/{vod_id}/cancel`                      | -                     | JSONResponse      |
| POST     | `/vods/{vod_id}/retry`                       | -                     | JSONResponse      |
| GET      | `/vods/{vod_id}/file`                        | -                     | FileResponse      |
| GET      | `/vods/{vod_id}/stream-download`             | -                     | StreamingResponse |
| DELETE   | `/vods/{vod_id}`                             | -                     | JSONResponse      |
| GET      | `/vods/{vod_id}/log`                         | -                     | JSONResponse      |

### twitch_status_router (Prefix `/api/twitch`)

| Methode  | Pfad                                         | Request-Modell        | Response-Modell   |
|----------|----------------------------------------------|-----------------------|-------------------|
| GET      | `/status`                                    | -                     | JSONResponse      |

### media_processing_api (Prefix `/api`)

| Methode  | Pfad                                         | Request-Modell             | Response-Modell   |
|----------|----------------------------------------------|----------------------------|-------------------|
| GET      | `/transcription/status`                      | -                          | JSONResponse      |
| POST     | `/transcription/preload-model`               | -                          | JSONResponse      |
| POST     | `/transcriptions`                            | StartTranscriptionRequest  | JSONResponse      |
| POST     | `/transcriptions/upload`                     | UploadFile + query params  | JSONResponse      |
| GET      | `/library/uploads`                           | -                          | JSONResponse      |
| GET      | `/library/uploads/{upload_id}/file`          | -                          | FileResponse      |
| DELETE   | `/library/uploads/{upload_id}`               | -                          | JSONResponse      |
| GET      | `/transcriptions`                            | query params               | JSONResponse      |
| GET      | `/transcriptions/{transcription_id}`         | -                          | JSONResponse      |
| POST     | `/transcriptions/{id}/cancel`                | -                          | JSONResponse      |
| POST     | `/transcriptions/{id}/retry`                 | -                          | JSONResponse      |
| DELETE   | `/transcriptions/{id}`                       | -                          | JSONResponse      |
| GET      | `/transcriptions/{id}/json`                  | -                          | FileResponse      |
| GET      | `/transcriptions/{id}/txt`                   | -                          | FileResponse      |
| GET      | `/transcriptions/{id}/srt`                   | -                          | FileResponse      |
| GET      | `/transcriptions/{id}/vtt`                   | -                          | FileResponse      |
| GET      | `/vods/{vod_id}/transcriptions`              | -                          | JSONResponse      |
| GET      | `/sources/{source_type}/{source_id}/transcriptions` | -                   | JSONResponse      |
| GET      | `/vods/{vod_id}/artifacts/audio`             | -                          | JSONResponse      |
| POST     | `/vods/{vod_id}/artifacts/audio`             | StartAudioExtractionReq    | JSONResponse      |
| GET      | `/vods/{vod_id}/artifacts/audio/file`        | -                          | FileResponse      |
| GET      | `/sources/{source_type}/{source_id}/artifacts/audio` | -                   | JSONResponse      |
| POST     | `/sources/{source_type}/{source_id}/artifacts/audio` | StartAudioExtractionReq | JSONResponse   |
| GET      | `/sources/{source_type}/{source_id}/artifacts/audio/file` | -                | FileResponse      |
| POST     | `/pipeline-runs`                             | StartPipelineRunRequest    | JSONResponse      |
| GET      | `/pipeline-runs`                             | query params               | JSONResponse      |
| GET      | `/pipeline-runs/{id}`                        | -                          | JSONResponse      |
| POST     | `/pipeline-runs/{id}/cancel`                 | -                          | JSONResponse      |
| POST     | `/pipeline-runs/{id}/retry`                  | -                          | JSONResponse      |
| DELETE   | `/pipeline-runs/{id}`                        | -                          | JSONResponse      |
| GET      | `/vods/{vod_id}/pipeline-runs`               | -                          | JSONResponse      |

### library_api (Prefix `/api/library`)

| Methode  | Pfad                                         | Request-Modell   | Response-Modell   |
|----------|----------------------------------------------|------------------|-------------------|
| GET      | `/items`                                     | -                | JSONResponse      |
| GET      | `/items/{item_id}`                           | -                | JSONResponse      |
| GET      | `/items/{item_id}/file`                      | -                | FileResponse      |
| POST     | `/uploads`                                   | UploadFile       | JSONResponse      |
| DELETE   | `/items/{item_id}`                           | -                | JSONResponse      |

### asr_api (Prefix `/api/asr`)

| Methode  | Pfad                                         | Request-Modell             | Response-Modell   |
|----------|----------------------------------------------|----------------------------|-------------------|
| GET      | `/presets`                                   | -                          | JSONResponse      |
| GET      | `/models`                                    | -                          | JSONResponse      |
| GET      | `/status`                                    | -                          | JSONResponse      |
| GET      | `/default`                                   | -                          | JSONResponse      |
| POST     | `/default`                                   | SelectDefaultRequest       | JSONResponse      |
| GET      | `/audio-diagnostics/{source_type}/{source_id}` | -                        | JSONResponse      |
| POST     | `/audio-diagnostics`                         | CreateAudioDiagnosticReq   | JSONResponse      |
| GET      | `/audio-diagnostics/{id}/artifacts/{variant}` | -                        | FileResponse      |
| POST     | `/benchmarks`                                | CreateBenchmarkRequest     | JSONResponse      |
| GET      | `/benchmarks`                                | -                          | JSONResponse      |
| GET      | `/benchmarks/{id}`                           | -                          | JSONResponse      |
| POST     | `/benchmarks/{id}/start`                     | -                          | JSONResponse      |
| POST     | `/benchmarks/{id}/cancel`                    | -                          | JSONResponse      |
| DELETE   | `/benchmarks/{id}`                           | -                          | JSONResponse      |
| POST     | `/benchmarks/{id}/select-default`            | SelectDefaultRequest       | JSONResponse      |
| GET      | `/benchmarks/{id}/runs/{preset_id}`          | -                          | JSONResponse      |

**Gesamt:** 75 oeffentliche API-Routen + 2 SPA-Routen.

---

## 5. Globale Instanzen und Import-Side-Effects

### In `app.py` beim Modul-Import erzeugte Instanzen:

1. `DATA_DIR` — `Path(os.environ.get("TTVTURBO_DATA_DIR") or (BASE_DIR / "data"))`; **mkdir bei Import**
2. `TTVTURBO_LIBRARY_DIR` — `Path(os.environ.get("TTVTURBO_LIBRARY_DIR") or (DATA_DIR / "library"))`
3. `library_storage` — `LibraryStorage(TTVTURBO_LIBRARY_DIR)`; **mkdir bei Konstruktion**
4. `library_service` — `LibraryService(library_storage)`
5. `RECORDINGS_DIR` — `DATA_DIR / "recordings"`; **mkdir bei Import**
6. `VOICE_CLONES_DIR` — `DATA_DIR / "voice_clones"`
7. `gpu_lock` — `GpuLock(DATA_DIR)`; **stale-lock reaping bei Konstruktion**
8. `voice_clone_service` — `VoiceCloneService(...)`; **mkdir + _recover_on_startup bei Konstruktion**
9. `VOICE_PROFILES_DIR` — `Path(os.environ.get(...) or (DATA_DIR / "voice_profiles"))`; **mkdir bei Import**
10. `voice_profile_service` — `build_voice_profile_service(...)`; **laedt Script-Pack bei Konstruktion**
11. `_voice_profile_quality_analyzer` — `make_voice_profile_quality_analyzer(voice_clone_service)`
12. `voice_profiles_router` — `build_voice_profiles_router(...)`
13. `TTVTURBO_VOD_DOWNLOAD_DIR` — `Path(os.environ.get(...) or (DATA_DIR / "vods"))`
14. `vod_pipeline_service` — `build_vod_pipeline_service(...)`; **_recover_on_startup bei Konstruktion**
15. `vod_pipeline_router` — `build_vod_pipeline_router(vod_pipeline_service)`
16. `twitch_status_router` — `build_twitch_status_router(vod_pipeline_service)`
17. `media_job_storage` — `MediaJobStorage(DATA_DIR)`; **mkdir bei Konstruktion**
18. `TTVTURBO_UPLOADS_DIR` — `Path(os.environ.get(...) or (DATA_DIR / "uploads"))`
19. `upload_storage` — `UploadStorage(TTVTURBO_UPLOADS_DIR)`; **mkdir bei Konstruktion**
20. `media_source_resolver` — `MediaSourceResolver(...)`
21. `audio_extraction_service` — `AudioExtractionService(...)`; **_recover_on_startup bei Konstruktion**
22. `asr_default_preset_store` — `AsrDefaultPresetStore(DATA_DIR)`; **mkdir bei Konstruktion**
23. `transcription_service` — `TranscriptionService(...)`; **_recover_on_startup bei Konstruktion**
24. `audio_extraction_service._on_job_ready` — **Side-Effect: Callback-Injection nach Konstruktion**
25. `pipeline_service` — `PipelineService(...)`
26. `media_processing_router` — `build_media_processing_router(...)`
27. `library_router` — `build_library_router(library_service)`
28. `asr_benchmark_service` — `AsrBenchmarkService(...)`; **mkdir + _recover_on_startup bei Konstruktion**
29. `audio_forensics_service` — `AudioForensicsService(...)`; **mkdir bei Konstruktion**
30. `asr_router` — `build_asr_router(...)`
31. `voice_clone_service.set_profile_reference_resolver(_resolve_profile_reference)` — **Side-Effect: Resolver-Injection nach Konstruktion**
32. `app` — `FastAPI(title=APP_NAME)`
33. 6x `app.include_router(...)` — **Router-Registrierung bei Import**

**Side-Effects bei `import app`:** Mindestens 8 Verzeichnis-Erstellungen, 4 `_recover_on_startup`-Aufrufe, 1 Script-Pack-Laden, 1 GPU-Lock-Reaping, 2 Callback-Injections, 6 Router-Registrierungen.

### Import-Kette (`from app import ...`):

- `app.py` importiert: `voice_clone.schemas`, `voice_clone.service`, `voice_profiles_api`, `vod_pipeline_api`, `media_processing`, `media_processing_api`, `asr_api`, `library`, `library_api`
- Jeder `*_api`-Import importiert sein jeweiliges Backend-Modul
- `media_processing.__init__` importiert 11 Submodule (alle ausser Workern)
- **Kein zirkulaerer Import gefunden.**

---

## 6. Worker mit aktuellem Startbefehl

| Worker                                  | Startbefehl                                              | Aufgerufen von                         |
|-----------------------------------------|----------------------------------------------------------|----------------------------------------|
| voice_clone.runtime                     | `python -m voice_clone.runtime <job_path>`               | voice_clone/service.py:721            |
| vod_pipeline.downloader_worker          | `python -m vod_pipeline.downloader_worker <job_path>`    | vod_pipeline/service.py:795           |
| media_processing.audio_extraction_worker| `python -m media_processing.audio_extraction_worker <job_path>` | media_processing/audio_extraction.py:390 |
| media_processing.transcription_worker   | `python -m media_processing.transcription_worker <job_path>` | media_processing/transcription.py:692 |
| media_processing.asr_benchmark_worker   | `python -m media_processing.asr_benchmark_worker <job_path>` | media_processing/asr_benchmark.py:350 |

Alle Worker verwenden `sys.executable` (nicht hartkodiert `python`).

**Ausnahme:** `vod_pipeline_api.py:411` verwendet `subprocess.Popen(["python", "-m", "yt_dlp", ...])` fuer Stream-Download (hartkodiert `python`, nicht `sys.executable`).

---

## 7. Alle Runtime-Datenpfade mit lesenden und schreibenden Modulen

### `data/` (TTVTURBO_DATA_DIR, default: `BASE_DIR / "data"`)

| Pfad                        | Schreibend                                     | Lesend                                          |
|-----------------------------|------------------------------------------------|-------------------------------------------------|
| `data/recordings/`          | app.py (upload_recording, delete_recording)    | app.py (_list_recordings, get_recording), conftest.py (isolated_recordings), verify.py |
| `data/voice_clones/`        | voice_clone/service.py                         | voice_clone/service.py                          |
| `data/voice_profiles/`      | voice_profiles/storage.py                      | voice_profiles/storage.py, voice_profiles/service.py |
| `data/twitch_profiles/`     | vod_pipeline/storage.py                        | vod_pipeline/storage.py, vod_pipeline/service.py |
| `data/vods/`                | vod_pipeline/storage.py, vod_pipeline/downloader_worker.py | vod_pipeline/storage.py, vod_pipeline/service.py, media_processing/sources.py, media_processing/audio_extraction.py, media_processing/transcription.py |
| `data/library/`             | library/storage.py, library/service.py, library_api.py, media_processing_api.py, migrate_to_library.py | library/storage.py, library/service.py, library_api.py, media_processing/sources.py, vod_pipeline/service.py |
| `data/media_jobs/`          | media_processing/storage.py, audio_extraction.py, transcription.py | media_processing/storage.py, audio_extraction.py, transcription.py, pipeline.py |
| `data/pipeline_runs/`       | media_processing/storage.py, pipeline.py       | media_processing/storage.py, pipeline.py        |
| `data/uploads/`             | media_processing/uploads.py                    | media_processing/uploads.py, media_processing/sources.py |
| `data/asr_benchmarks/`      | media_processing/asr_benchmark.py              | media_processing/asr_benchmark.py, asr_api.py   |
| `data/audio_diagnostics/`   | media_processing/audio_forensics.py            | media_processing/audio_forensics.py, asr_api.py |
| `data/asr_default_preset.json` | media_processing/asr_presets.py             | media_processing/asr_presets.py, transcription.py |
| `data/gpu.lock`             | media_processing/gpu_lock.py                   | media_processing/gpu_lock.py                    |

### Frontend-Build-Pfad

| Pfad                   | Schreibend                  | Lesend          |
|------------------------|-----------------------------|-----------------|
| `frontend/dist/`       | npm build (subprocess)      | app.py (SPA)    |

### Temporaere Pfade

| Pfad                              | Schreibend     | Lesend     |
|-----------------------------------|----------------|------------|
| `tempfile.gettempdir()/ttvturbo_*`| app.py         | app.py     |

---

## 8. Frontend-Routen und Navigation

### Verfuegbare Seiten (mit AppLayout):

| Pfad                       | Seite                  | Status      |
|----------------------------|------------------------|-------------|
| `/dashboard`               | DashboardPage          | available   |
| `/vod-pipeline`            | VodPipelinePage        | available   |
| `/vod-pipeline/:vodId`     | VodDetailPage          | available   |
| `/vod-downloader`          | VodDownloaderPage      | available   |
| `/transcription`           | TranscriptionPage      | available   |
| `/voice-clone`             | VoiceClonePage         | available   |
| `/library`                 | LibraryPage            | available   |
| `/library/:itemId`         | LibraryDetailPage      | available   |
| `/voice-profiles`          | VoiceProfilesPage      | available   |
| `/twitch-profiles`         | TwitchProfilesPage     | available   |
| `/settings`                | SettingsPage           | partial     |

### Unavailable-Seiten (UnavailablePage):

| Pfad                       | Titel              |
|----------------------------|--------------------|
| `/clips`                   | Clip-Vorschlaege   |
| `/ideas`                   | Ideen              |
| `/recording-studio`        | Aufnahmestudio     |
| `/synthetic-studio`        | Synthetic Studio   |
| `/editor`                  | Video Editor       |
| `/layouts`                 | Layout Studio      |
| `/automations`             | Automationen       |
| `/publishing`              | Veroeffentlichungen|

### Weiterleitungen:

| Pfad            | Ziel             |
|-----------------|------------------|
| `/`             | `/dashboard`     |
| `/vod-explorer` | `/vod-downloader`|
| `*`             | NotFoundPage     |

### Frontend-API-Clients:

- `api/client.ts` — zentraler `apiClient` mit `get`, `post`, `delete`
- `api/recordings.ts` — `/api/recordings`
- `api/status.ts` — `/api/status`
- `api/voiceClone.ts` — `/api/voice-clone/*`
- `features/asrComparison/api.ts` — `/api/asr/*`
- `features/library/api.ts` — `/api/library/*`
- `features/mediaProcessing/api.ts` — `/api/transcriptions/*`, `/api/pipeline-runs/*`, `/api/vods/*`, `/api/sources/*`
- `features/vodPipeline/api.ts` — `/api/twitch/*`, `/api/vods/*`
- `features/voiceProfiles/api.ts` — `/api/voice-profiles/*`

---

## 9. Private Cross-Module-Zugriffe

### Bestaetigte private Zugriffe (ueber Modulgrenzen hinweg):

1. **`media_processing_api.py:230`** — `library_service.storage._item_dir(upload_id)` (noqa: SLF001)  
   Zugriff auf private Methode `_item_dir` von `LibraryStorage` aus dem API-Modul.

2. **`library_api.py:91`** — `service.storage._item_dir(item_id)` (noqa: SLF001)  
   Zugriff auf private Methode `_item_dir` von `LibraryStorage` aus dem API-Modul.

3. **`media_processing/sources.py:122`** — `self.vod_storage._vod_dir(vod_id)` (noqa: SLF001)  
   Zugriff auf private Methode `_vod_dir` von `VodPipelineStorage` aus `media_processing`.

4. **`media_processing/sources.py:183`** — `self.library_service.storage._item_dir(upload_id)` (noqa: SLF001)  
   Zugriff auf private Methode `_item_dir` von `LibraryStorage` aus `media_processing`.

5. **`media_processing_api.py:230`** — `library_service.storage._item_dir(upload_id)`  
   Auch in `upload_and_transcribe` (gleiche Datei, weitere Stelle).

6. **`migrate_to_library.py:133`** — `library_service.storage._item_dir(lib_meta["id"])` (noqa: SLF001)  
   Zugriff auf private Methode aus Migrationsskript.

7. **`migrate_to_library.py:71`** — `library_service.storage._read_json.__doc__`  
   Zugriff auf private Methode `_read_json` (und deren `__doc__`-Attribut) — **BUG** (siehe 11.5).

8. **`asr_api.py:286`** — `benchmark_service._runs_dir(benchmark_id)` (noqa: SLF001)  
   Zugriff auf private Methode `_runs_dir` von `AsrBenchmarkService` aus dem API-Modul.

9. **`asr_api.py:284`** — `from media_processing.asr_benchmark import _read_json` (noqa: PLC0415)  
   Import einer privaten Funktion `_read_json` aus `asr_benchmark`.

10. **`app.py:192`** — `audio_extraction_service._on_job_ready = transcription_service.on_audio_ready` (noqa: SLF001)  
    Direktes Setzen eines privaten Attributs `_on_job_ready` nach Konstruktion.

11. **`media_processing/audio_extraction.py:181`** — `self._on_job_ready`  
    Private Attribut, das von aussen gesetzt wird (siehe app.py:192).

---

## 10. Bestaetigte Duplikate und vermutete Duplikate

### Bestaetigte Duplikate:

1. **Legacy Upload-Endpoints** — `media_processing_api.py:282-354` definiert `/api/library/uploads`, `/api/library/uploads/{id}/file`, `DELETE /api/library/uploads/{id}` als Legacy-Wrapper, die an `library_service` delegieren. Diese Routen duplizieren funktional `library_api.py` (`/api/library/items`, `/api/library/uploads`, `/api/library/items/{id}/file`).

2. **`_error_response`-Funktion** — In `voice_profiles_api.py:65`, `vod_pipeline_api.py:70`, `media_processing_api.py:102`, `library_api.py:32`, `asr_api.py:82` — jeweils identisch implementiert (5x).

3. **`_atomic_write_json`-Funktion** — In `vod_pipeline/storage.py:117`, `library/storage.py:98`, `media_processing/storage.py:63`, `media_processing/asr_benchmark.py:116` — jeweils aehnlich implementiert (4x, mit leicht unterschiedlichen Retry-Logiken und Fehlertypen).

4. **`_validate_uuid`-Funktion** — In `vod_pipeline/storage.py:57`, `library/storage.py:53`, `media_processing/storage.py:48` — jeweils aehnlich implementiert (3x).

5. **`_read_json`-Funktion** — In `vod_pipeline/storage.py:184`, `library/storage.py:151`, `media_processing/storage.py:98`, `media_processing/asr_benchmark.py:129` — jeweils aehnlich implementiert (4x).

6. **`_now_iso`-Funktion** — In `library/storage.py:49`, `library/service.py` (importiert), `media_processing/asr_benchmark.py:100`, `media_processing/asr_presets.py`, `media_processing/audio_forensics.py:76`, `media_processing/uploads.py:37` — jeweils identisch implementiert (6x).

7. **`_find_executable` / `_ffmpeg_available`** — In `app.py:308-345`; aehnliche FFmpeg/FFprobe-Suche auch in `media_processing/audio_extraction.py` und `media_processing/audio_forensics.py` (ueber `shutil.which`).

### Vermutete Duplikate (nicht bestaetigt):

1. **Audio-Qualitaetsanalyse** — `voice_clone/quality.py` wird sowohl von `voice_clone/service.py` als auch von `voice_profiles_api.py` (via `make_quality_analyzer`) verwendet. Dies ist beabsichtigt (Delegation), kein echtes Duplikat, aber die Aufrufpfade sind unterschiedlich.

2. **FFprobe-Inspektion** — `vod_pipeline/service.py:1174` (`ffprobe_inspect`) und `media_processing/audio_extraction.py:129` und `media_processing/audio_forensics.py:152` — alle fuehren aehnliche `ffprobe`-Aufrufe durch, aber mit unterschiedlichen Auswertungen.

3. **VAD-Regionen** — `media_processing/asr_diagnostics.py` wird sowohl von `asr_benchmark.py` als auch von `audio_forensics.py` verwendet. Dies ist beabsichtigt (Wiederverwendung), kein Duplikat.

---

## 11. Bekannte Fehler erneut verifiziert oder widerlegt

### 11.1 Library-Datei nach VOD-Promotion nicht aufloesbar

**Status: WIDERLEGT (im aktuellen Code behandelt)**

`vod_pipeline/service.py` hat drei Schutzmechanismen:
- `_ready_source_exists(vod)` (Zeile 354-368): Prueft `library_item_id` zuerst, dann VOD-Dir.
- `_library_item_file_exists(library_item_id)` (Zeile 370-377): Prueft, ob die Library-Datei existiert.
- `ready_file_path(vod_id)` (Zeile 1070-1079): Bevorzugt Library-Datei, faellt auf VOD-Dir zurueck.
- `_finalize_download` (Zeile 920-968): Behandelt dangling `library_item_id` (Zeile 928-938) und re-promotet die Datei.

**Aber:** `media_processing/sources.py:122-124` loest die VOD-Quelle ueber `vod_dir / file_name` auf, **ohne** `library_item_id` zu beruecksichtigen. Wenn die Datei in die Library verschoben wurde, schlaegt dies fehl mit "source file is missing on disk". Dies ist ein **potenzieller Bug** fuer Media-Processing (Audio-Extraktion, Transkription) nach VOD-Promotion.

### 11.2 Pauschales *.tmp-Cleanup

**Status: BESTAETIGT (eingeschraenkt auf Recordings)**

`app.py:369-379` (`_is_temp_or_hidden`) filtert Dateien mit den Suffixen `.tmp`, `.part`, `.bak`, `.swp` aus der Recordings-Liste. Dies ist **nicht** ein globales Cleanup (keine Datei-Loeschung), sondern ein Lesefilter fuer `/api/recordings`. Die Storage-Module verwenden eindeutige tmp-Namen (`.{name}.{pid}.{ns}.tmp`) und loeschen sie selbst nach `os.replace`. **Kein pauschales Loeschen von *.tmp-Dateien gefunden.**

### 11.3 Testimport schreibt moeglicherweise in echtes data/

**Status: BESTAETIGT**

`tests/conftest.py:47-48` liefert `TestClient(app_module.app)` und `app_module.RECORDINGS_DIR` direkt an Tests. Da `import app` die globalen Instanzen mit `DATA_DIR = BASE_DIR / "data"` erzeugt, schreiben Tests, die `isolated_recordings` oder `client` verwenden, in das echte `data/recordings/` Verzeichnis.

**Konkrete Stellen:**
- `conftest.py:74-90` (`isolated_recordings`): Erstellt WAV-Dateien in `app_module.RECORDINGS_DIR` (echtes data/).
- `conftest.py:52-53` (`recordings_dir`): Gibt echtes `RECORDINGS_DIR` an Tests.
- `verify.py:32,112,160-161`: Schreibt in echtes `RECORDINGS_DIR`.

**Eingrenzung:** Die VOD- und Media-Processing-Tests verwenden `tmp_path`-basierte Fixtures (`vod_data_dir`, `vod_download_dir`), die nicht in echtes data/ schreiben. Nur Recordings-Tests sind betroffen.

### 11.4 Runtime-Installation von Dependencies

**Status: BESTAETIGT**

`media_processing/transcription_worker.py:113-146` fuehrt ein On-Demand `pip install` durch:
- Prueft ob `faster_whisper` importierbar ist.
- Wenn nicht: `pip_args = [sys.executable, "-m", "pip", "install", ...]`
- Installiert `faster-whisper` und ggf. `torch` (mit `--index-url` fuer CUDA).
- Timeout: 1800 Sekunden (30 Minuten).

Dies geschieht **im Worker-Subprozess**, nicht im FastAPI-Prozess. Die Installation ist nicht deklarativ (nicht in `requirements.txt` oder `requirements-gpu.txt` erfasst als bedingte Dependency).

### 11.5 Unsicheres migrate_to_library.py

**Status: BESTAETIGT (BUG in Zeile 71)**

`migrate_to_library.py:71`:
```python
meta["updated_at"] = library_service.storage._read_json.__doc__ or meta.get("updated_at")
```

Dies setzt `updated_at` auf den Docstring der privaten Methode `_read_json` (oder behaelt den alten Wert, wenn der Docstring leer ist). Der Docstring von `_read_json` in `library/storage.py:151` ist nicht vorhanden (die Methode hat keinen Docstring), also ist `__doc__` `None` und der `or`-Ausdruck faellt auf `meta.get("updated_at")` zurueck. **Der Code funktioniert zufaellig richtig, ist aber semantisch falsch** — es sieht aus, als sollte ein Zeitstempel gesetzt werden, aber stattdessen wird ein Docstring-Attribut gelesen.

Zusaetzlich:
- Zeile 133: `library_service.storage._item_dir(lib_meta["id"])` — privater Zugriff (noqa: SLF001).
- Zeile 151: `sys.path.insert(0, str(repo_dir))` — Modifikation von `sys.path`.
- Keine Transaktionssicherheit: Wenn `shutil.move` (Zeile 134) fehlschlaegt, ist die Library-Item-Metadaten bereits gespeichert, aber die Datei fehlt.

---

## 12. Priorisierte Cleanup-Reihenfolge mit Abhaengigkeiten

### Phase 1: Sicherheit und Korrektheit (keine Refaktorisierung)

1. **migrate_to_library.py:71 Bug fixen** — Ersetze `library_service.storage._read_json.__doc__` durch `_now_iso()` oder `meta.get("updated_at")`. Keine Abhaengigkeiten.

2. **media_processing/sources.py: VOD-Promotion beruecksichtigen** — `_resolve_twitch_vod` muss `library_item_id` pruefen und die Datei aus der Library aufloesen, nicht nur aus dem VOD-Dir. Abhaengig von: `vod_pipeline.service` (liest `library_item_id`), `library.service` (liefert `item_file_path`).

3. **conftest.py: Recordings-Tests isolieren** — Verwende `tmp_path`-basiertes `RECORDINGS_DIR` statt `app_module.RECORDINGS_DIR`. Abhaengig von: `app.py` (erfordert entweder Umstrukturierung der globalen Instanzen oder Monkey-Patching im Test).

### Phase 2: Duplikate reduzieren (mit Testabdeckung)

4. **`_error_response` vereinheitlichen** — In ein gemeinsames Modul auslagern (z.B. `api_utils.py`). Abhaengig von: alle 5 API-Module.

5. **`_atomic_write_json` / `_read_json` / `_validate_uuid` / `_now_iso` vereinheitlichen** — In ein gemeinsames Storage-Utility-Modul auslagern. Abhaengig von: alle 4 Storage-Module + `asr_benchmark.py`.

### Phase 3: Private Zugriffe kapseln

6. **`_item_dir` oeffentlich machen oder Hilfsmethode einfuehren** — `LibraryStorage` sollte eine oeffentliche Methode wie `item_dir(item_id)` oder `write_item_file(item_id, file_name, content)` anbieten. Abhaengig von: `library_api.py`, `media_processing_api.py`, `media_processing/sources.py`, `migrate_to_library.py`.

7. **`_runs_dir` oeffentlich machen** — `AsrBenchmarkService` sollte eine oeffentliche Methode anbieten. Abhaengig von: `asr_api.py`.

8. **`_read_json` aus `asr_benchmark` oeffentlich machen oder ueber Service-Methode kapseln** — Abhaengig von: `asr_api.py`.

### Phase 4: Architektur-Bereinigung (niedrige Prioritaet)

9. **Legacy Upload-Endpoints entfernen** — Nach Migration aller Frontend-Calls auf `/api/library/*`. Abhaengig von: Frontend-Verifikation (keine Calls zu `/api/library/uploads/*`).

10. **`vod_pipeline_api.py:411` `python` durch `sys.executable` ersetzen** — Konsistenz mit allen anderen Worker-Starts.

11. **On-Demand pip install dokumentieren oder deklarativ machen** — `transcription_worker.py` sollte entweder Dependencies in `requirements.txt` deklarieren oder den Installationsmechanismus dokumentieren.

---

## Baseline-Testresultate

### Python

| Test                        | Ergebnis |
|-----------------------------|----------|
| `python -m compileall -q .` | **PASS** (0 Fehler) |
| `pytest --collect-only -q`  | **PASS** (446 Tests gesammelt) |
| `pytest -q`                 | **1 FAILED, 444 passed, 1 skipped** |

**Fehlgeschlagener Test:** `tests/test_asr_metrics.py::test_compute_metrics_empty_reference_all_insertions`  
**Ursache:** `jiwer`-Bibliothek behandelt leere Referenz-Strings als Fehler (`ValueError: one or more references are empty strings`). Der Test erwartet `m.available is True`, aber `compute_metrics` faellt auf `available=False` zurueck.  
**Klassifizierung:** **Bereits bestehender Testfehler** — verursacht durch eine jiwer-Version, die leere Referenzen als Fehler behandelt. Kein Codefehler, keine fehlende Dependency, kein externer E2E-Test.

**Uebersprungener Test:** 1 Test uebersprungen (E2E-Marker, benoetigt `TTVTURBO_RUN_QWEN_TTS_E2E=1`).

### Frontend

| Test                        | Ergebnis |
|-----------------------------|----------|
| `npm --prefix frontend ci`  | **PASS** (242 packages) |
| `npm --prefix frontend run typecheck` | **PASS** (0 Fehler) |
| `npm --prefix frontend run test`      | **PASS** (83 Tests in 11 Dateien) |
| `npm --prefix frontend run build`     | **PASS** (1818 Module, 690 KB JS) |

---

## Erstellte Dateien

- `docs/audits/repository-before-cleanup.md` — Dieses Dokument.
- `tests/contracts/api_routes_before.json` — Maschinenlesbarer API-Routen-Snapshot.

---

## Commit

```
Audit current routes data paths and module boundaries
```

---

## Pushstatus

Kein Push durchgefuehrt (wie im Auftrag gefordert: "Kein Push ungefragt").
