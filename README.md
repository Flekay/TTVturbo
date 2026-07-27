# TTVturbo

Browserbasiertes Dashboard für echte Mikrofonaufnahmen. Der Browser
nimmt das Mikrofon auf, der Server konvertiert die Browseraufnahme mit
FFmpeg in eine echte WAV-Datei (PCM 16-bit, 44,1 kHz, mono) und stellt
sie über ein React-Dashboard bereit.

## Architektur

```
TTVturbo/
├── app.py                # FastAPI-Backend: Aufnahmen, Voice Clone, Status, SPA-Auslieferung
├── voice_profiles_api.py # Voice-Profile FastAPI-Router + Service-Factory
├── vod_pipeline_api.py   # VOD-Pipeline FastAPI-Router (Twitch-Profile, VODs, Downloads, Status)
├── media_processing_api.py # Media-Processing FastAPI-Router (Transkription, Audio-Artefakte, Pipeline-Runs)
├── library_api.py        # Library FastAPI-Router (persistente Video-Sammlung, Uploads)
├── verify.py             # automatisierter Backend-Verifikationslauf
├── migrate_to_library.py # Migration: VOD-Downloads in die persistente Library übernehmen
├── voice_profiles/       # Voice-Profile-Kern (Library, Storage, Service, Schemas)
├── vod_pipeline/         # Twitch-VOD-Pipeline-Kern (Schemas, Storage, TwitchClient, Service, Downloader-Worker)
├── media_processing/     # Shared Media-Processing-Kern (Schemas, Storage, GPU-Lock, Sources, Audio-Extraktion, Transkription, Pipeline)
├── voice_clone/          # Qwen3-TTS Voice-Clone-Modul (Service, Runtime, Qualitätsanalyse, Diagnostics)
├── library/              # Persistente Video-Sammlung (Schemas, Storage, Service)
├── config/               # Voice-Pack-Skripte (config/voice_lab/scripts/de-DE/ttvturbo_voice_pack_v1.json)
├── data/                 # Single Runtime-Data-Root (konfigurierbar via TTVTURBO_DATA_DIR)
│   ├── recordings/       # erzeugte WAV-Dateien
│   ├── voice_clones/     # Voice-Clone-Generierungen (metadata.json + output.wav)
│   ├── voice_profiles/   # persistierte Profile (JSON)
│   ├── twitch_profiles/  # Twitch-Profile (JSON)
│   ├── vods/             # VOD-Metadaten + temporäre Downloads
│   ├── library/          # persistente Video-Sammlung (Downloads + Uploads)
│   ├── uploads/          # Media-Processing-Uploads
│   ├── media_jobs/       # Media-Processing-Jobs
│   └── pipeline_runs/    # Pipeline-Run-Records
├── tests/                # pytest-Backendtests
├── scripts/              # verify_local.ps1 + validate_voice_scripts.py
├── .github/workflows/    # ci.yml - GPU-freie CI (Python + Frontend + FFmpeg)
├── requirements.txt      # Basissystem (FastAPI, soundfile, numpy, psutil, yt-dlp)
├── requirements-dev.txt  # pytest + httpx
├── requirements-gpu.txt  # NVIDIA-/Qwen-/Whisper-Stack (torch+cu128, qwen-tts, transformers, faster-whisper)
│
├── frontend/             # React 19 + TypeScript + Vite Dashboard
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── vitest.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── router.tsx
│       ├── router.routes.tsx
│       ├── api/          # typisierter Fetch-Client + Endpunkte
│       ├── components/   # Layout, UI-Wrappers, Recordings, VoiceClone, ErrorBoundary
│       ├── pages/        # Dashboard, VoiceProfiles, VoiceClone, VodDownloader, VodPipeline, VodDetail, Transcription, TwitchProfiles, Library, Settings, NotFound, Unavailable
│       ├── features/     # library + voiceProfiles + vodPipeline + mediaProcessing Feature-Module (Schemas, API, Hooks, Panels)
│       ├── stores/       # Zustand UI-Store (localStorage)
│       ├── hooks/        # useRecorder, useBackendStatus, TanStack Query Hooks
│       ├── types/        # TypeScript-Typen + Zod-Schemata
│       ├── utils/        # Formatierung
│       ├── styles/       # zentrale CSS-Variablen + Komponenten-Styles
│       └── test/         # Vitest-Setup + Tests
│
└── README.md
```

### Technologie

Backend: Python, FastAPI, Uvicorn, FFmpeg.
Frontend: React 19, TypeScript, Vite, React Router, TanStack Query,
Zustand, React Hook Form, Zod, Radix UI, Lucide React.

## Voraussetzungen

- Python 3.10+ (getestet: 3.12.10 für das Basissystem und 3.12.10 für die
  NVIDIA-/Qwen-Unterstützung)
- Node.js 20+ und npm
- FFmpeg (und ffprobe) im PATH
- Ein Browser mit `MediaRecorder` und `getUserMedia` (Chrome, Edge, Firefox)

Wichtig: Das Basissystem kann ohne Qwen starten. Voice Cloning benötigt
eine funktionierende CUDA-Runtime. Die Installation ist in zwei Stufen
getrennt; nur die zweite Stufe erfordert eine NVIDIA-GPU.

## Basissystem installieren

Das Basissystem reicht für `python app.py`, das React-Dashboard, echte
Mikrofonaufnahmen, FFmpeg-WAV-Konvertierung, Aufnahmenbibliothek und die
Voice-Clone-Orchestrierung (Qualitätsanalyse, Status-Polling,
Restart-Recovery). Der Qwen3-TTS-Worker subprocess startet nur dann und
liefert echte Audios, wenn die GPU-Abhängigkeiten aus dem nächsten
Abschnitt installiert sind; ohne sie meldet der Statusdiagnose-Endpunkt
ehrlich `qwen_tts_importable=false` und eine Generierung wird `FAILED`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt   # nur fuer pytest / Backendtests
npm --prefix frontend ci
```

FFmpeg unter Windows z. B. mit:

```powershell
winget install --id=Gyan.FFmpeg -e
```

## NVIDIA- / Qwen- / Whisper-Unterstützung installieren (optional, RTX 5070)

Diese Stufe ist nur auf der Maschine nötig, die echte Voice-Clone-Generierungen
oder GPU-Transkriptionen ausführen soll. Sie installiert die CUDA-fähigen Wheels
für den `voice_clone.runtime`- und `media_processing.transcription_worker`-
Worker subprocess. Das FastAPI-Backend selbst importiert diese Pakete nie; es
startet auch ohne sie.

Getestet auf der Zielhardware (Status `REAL_MODEL_VERIFIED`):

| Eigenschaft         | Wert                                                |
|---------------------|-----------------------------------------------------|
| GPU                 | NVIDIA GeForce RTX 5070 (12 GB VRAM, Blackwell)     |
| RAM                 | 64 GB                                               |
| OS                  | Windows                                             |
| Python-Version      | 3.12.10                                             |
| PyTorch-Version     | 2.11.0+cu128                                        |
| CUDA-Wheel-Quelle   | `https://download.pytorch.org/whl/cu128`            |
| qwen-tts            | 0.1.1                                               |
| transformers        | 4.57.3                                              |
| accelerate          | 1.12.0                                              |
| RTX-5070-Teststatus | REAL_MODEL_VERIFIED (3 echte Generierungen)         |
| Peak-VRAM           | ~3.95 GB allocated / ~4.41 GB reserved (12 GB GPU)  |
| Modellladezeit      | 180 s kalt, ~5 s warm (Cache)                       |
| Generierungszeit    | ~5–6 s pro Satz                                     |

Wichtig: Der Standard-PyPI-Wheel von `torch` ist auf Windows CPU-only. Er
silently deaktiviert CUDA und der Worker meldet `cuda_available=false`.
Die CUDA-Builds müssen zwingend vom `cu128`-Index gezogen werden.

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements-gpu.txt --extra-index-url https://download.pytorch.org/whl/cu128
```

Speicherbedarf: plane ~4.5 GB Peak-VRAM für Modell + Generierung ein. Die
12 GB RTX 5070 hat ausreichend Headroom. Nach `gc.collect()` +
`torch.cuda.empty_cache()` + `torch.cuda.ipc.collect()` fällt der
allozierte VRAM auf ~9 MB, aber ~322 MB bleiben vom CUDA-Caching-Allocator
reserviert (erwartet, kein Leak). Nur das Beenden des Worker-Subprocess
gibt den gesamten VRAM an das OS zurück.

Diagnostikbefehl (lädt kein Modell, prüft nur die Runtime-Voraussetzungen):

```powershell
python -m voice_clone.diagnostics
```

Der Diagnose-Endpunkt ist auch zur Laufzeit verfügbar:

```powershell
curl http://127.0.0.1:8765/api/voice-clone/status
```

Beide zeigen ehrlich an, ob `qwen_tts` importierbar ist, ob CUDA verfügbar
ist, welchen Device-Namen und welche VRAM-Werte `torch` meldet, und ob
FFmpeg/soundfile/Datenverzeichnis funktionieren. Sie erfinden keine Werte.

### Transkription (faster-whisper)

Die GPU-Transkription nutzt `faster-whisper` und wird ebenfalls über
`requirements-gpu.txt` installiert. Der Transkriptions-Worker läuft in einem
eigenen Subprocess und teilt sich die GPU mit dem Voice-Clone-Worker über
einen projektweiten Cross-Process-GPU-Lock (`data/gpu.lock`). Die
beiden Worker laden nie gleichzeitig ihre Modelle.

Konfiguration via Umgebungsvariablen (siehe `.env.example`):

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `TTVTURBO_TRANSCRIPTION_MODEL` | `large-v3` | faster-whisper Modell |
| `TTVTURBO_TRANSCRIPTION_DEVICE` | `cuda` | `cuda` oder `cpu` |
| `TTVTURBO_TRANSCRIPTION_COMPUTE_TYPE` | `int8_float16` | Compute-Type |
| `TTVTURBO_TRANSCRIPTION_LANGUAGE` | `de` | Sprache oder `auto` |
| `TTVTURBO_MAX_CONCURRENT_TRANSCRIPTIONS` | `1` | Max. parallele Jobs |

Diagnostik-Endpunkt:

```powershell
curl http://127.0.0.1:8765/api/transcription/status
```

### VOD Pipeline

Die VOD Pipeline orchestriert Download → Audio-Extraktion → Transkription
in einem automatisierten Ablauf. Sie verwendet die gleichen Services wie
die On-Demand-Seiten (VOD Downloader, Transkription) und implementiert keine
eigene Download-, Audio- oder Transkriptionslogik. Die Clip-Suche
(Pipeline-Schritt `FIND_CLIPS`) ist in dieser Phase als `NOT_IMPLEMENTED`
markiert.

## Entwicklungsstart

Zwei Terminals:

```powershell
# Terminal 1 – Backend
uvicorn app:app --reload --host 127.0.0.1 --port 8765

# Terminal 2 – Frontend (Vite Dev-Server mit Proxy auf /api)
npm --prefix frontend run dev
```

Vite läuft auf `http://127.0.0.1:5173` und leitet `/api` an das Backend
auf Port 8765 weiter.

## Frontendbuild

```powershell
npm --prefix frontend run build
```

Erzeugt `frontend/dist/`. FastAPI liefert die gebauten Dateien danach
direkt aus, inklusive SPA-Fallback für unbekannte Routen.

## Lokaler Produktionsstart

```powershell
python app.py
```

Zugriff: `http://127.0.0.1:8765`

FastAPI liefert bei Frontendrouten `index.html`, aber niemals
API-404s auf das Frontend um.

## Dashboardrouten

| Route               | Seite                | Status                       |
|---------------------|----------------------|------------------------------|
| `/dashboard`        | Dashboard            | funktionsfähig               |
| `/vod-pipeline`     | VOD Pipeline         | funktionsfähig               |
| `/vod-downloader`   | VOD Downloader       | funktionsfähig               |
| `/transcription`    | Transkription        | funktionsfähig               |
| `/voice-clone`      | Voice Clone          | funktionsfähig               |
| `/library`          | Bibliothek           | funktionsfähig               |
| `/voice-profiles`   | Voice Profiles       | funktionsfähig               |
| `/twitch-profiles`  | Twitch-Profile       | funktionsfähig               |
| `/settings`         | Einstellungen        | teilweise funktionsfähig     |
| `/vod-explorer`     | (Weiterleitung)      | leitet auf `/vod-downloader` |
| `/clips`            | Clip-Vorschläge      | noch nicht implementiert     |
| `/ideas`            | Ideen                | noch nicht implementiert     |
| `/recording-studio` | Aufnahmestudio       | noch nicht implementiert     |
| `/synthetic-studio` | Synthetic Studio     | noch nicht implementiert     |
| `/editor`           | Video Editor         | noch nicht implementiert     |
| `/layouts`          | Layout Studio        | noch nicht implementiert     |
| `/automations`      | Automationen         | noch nicht implementiert     |
| `/publishing`       | Veröffentlichungen   | noch nicht implementiert     |
| `/*`                | 404                  | funktionsfähig               |

Nicht implementierte Module zeigen eine ehrliche Vorschauseite ohne
Fake-Daten oder funktionslose Aktionen.

## Voice-Profiles-Funktion

Die Voice-Profiles-Seite (`/voice-profiles`) verwalten Voice-Profile und
die Referenzaufnahmen dafür. Aufnahmen erfolgen über den Dashboard-Recorder
oder direkt pro Prompt inline; Voice Clone ist ein eigenes Modul unter
`/voice-clone`.

### Aufnahmen

1. Mikrofonberechtigung im Browser erlauben.
2. Audiogerät auswählen.
3. Aufnahme starten / stoppen.
4. Browseraufnahme wird an FastAPI gesendet.
5. FFmpeg konvertiert zu WAV (PCM 16-bit, 44,1 kHz, mono).
6. WAV wird unter `data/recordings/` gespeichert.
7. Aufnahme erscheint in der Bibliothek.
8. Audio abspielen, herunterladen oder löschen.
9. Dashboardstatistiken aktualisieren sich automatisch über TanStack Query.

Der Recorder kapselt Berechtigungsanfrage, Geräteauswahl, MediaRecorder,
Dauer, Live-Pegel (Web Audio `AnalyserNode`), Blob-Erzeugung und Upload
im `useRecorder`-Hook.

### Voice Clone (Qwen3-TTS)

Voice Clone unterstützt zwei Modi:

**Manuelle Referenz** (klassisch):
1. Referenzaufnahme aus der Bibliothek wählen.
2. Der Server analysiert die technische Qualität der Referenz
   (Pegel, Stille, SNR, Dropouts, Clipping, NaN/Inf) und stuft sie als
   `EXCELLENT`, `GOOD`, `REVIEW` oder `REJECT` ein.
3. Exakt gesprochenen Referenztext und neuen Zieltext (max. 300 Zeichen)
   eingeben.
4. Bei `REVIEW` muss die Qualitätswarnung explizit bestätigt werden;
   bei `REJECT` ist die Generierung blockiert.

**Aus Voice-Profil**:
1. Ein Voice-Profil und eine akzeptierte Referenz daraus wählen.
2. Der Server löst die WAV-Datei und den Skripttext automatisch auf; der
   Client kann beides nicht überschreiben.
3. Nur Zieltext eingeben (max. 300 Zeichen).

In beiden Modi gilt:
- Beim Start wird ein eigener Subprocess mit dem
  `Qwen/Qwen3-TTS-12Hz-1.7B-Base`-Modell auf der RTX 5070 gestartet.
  Der Status (QUEUED → VALIDATING_REFERENCE → LOADING_MODEL →
  GENERATING → VALIDATING_OUTPUT → READY/FAILED) wird live im Dashboard
  gepollt.
- Es läuft höchstens eine Generierung gleichzeitig (Server-Lock). Eine
  zweite Anfrage wird mit HTTP 409 abgelehnt.
- Nach Abschluss erscheint die Generierung im Tab **Generierungen** mit
  Audio-Player, Download und Löschen-Button.
- Generierungen, die beim Server-Neustart in einem transienten Status
  waren, werden automatisch auf `FAILED` mit Begründung gesetzt und
  partielle Outputs werden entfernt.

Die Voice-Clone-Artefakte liegen unter `data/voice_clones/<id>/` mit
`metadata.json` und `output.wav`. Path-Traversal ist auch hier blockiert.

### Voice Profiles

Voice Profiles verwalten strukturierte Referenzaufnahmen für Voice Clones:

1. Ein Profil erstellen (Name + Locale, z. B. `de-DE`).
2. Das Profil enthält 88 Aufnahmeskripte aus dem Voice Pack
   (`config/voice_lab/scripts/de-DE/ttvturbo_voice_pack_v1.json`).
3. Pro Skript eine geführte Aufnahme starten: der exakte Skripttext wird
   im Aufnahmen-Tab angezeigt, nach erfolgreichem Upload wird die WAV
   automatisch mit dem Profil verknüpft.
4. Der Server analysiert jede Referenz mit dem echten
   `voice_clone.quality`-Analyzer und stuft sie als `ACCEPTED`, `REVIEW`
   oder `REJECTED` ein. `REVIEW`-Referenzen können manuell akzeptiert
   werden.
5. Der Fortschrittsbalken zeigt akzeptierte/review/abgelehnte/fehlende
   Referenzen sowie `clone-ready` (mindestens eine akzeptierte Referenz)
   und `pack vollständig` (alle 88 akzeptiert).
6. Im Voice-Clone-Tab kann direkt aus einem Profil generiert werden,
   ohne Referenztext manuell eingeben zu müssen.
7. Aufnahmen, die von einem Profil referenziert werden, können nicht
   gelöscht werden (HTTP 409 mit Profil-Liste). Die Verknüpfung muss
   zuerst entfernt werden.

Profile liegen als JSON-Dateien unter `data/voice_profiles/` (konfigurierbar
über `TTVTURBO_VOICE_PROFILES_DIR`). Die zugrunde liegenden WAV-Dateien
bleiben beim Löschen eines Profils erhalten.

## VOD-Pipeline-Funktion (Phase 1)

Die VOD Pipeline (`/vod-pipeline`) synchronisiert Twitch-VODs und -Clips
und lädt sie herunter. Sie nutzt ausschließlich `yt-dlp` – keine Twitch-API-
Credentials, kein Client-ID/Secret, kein OAuth. yt-dlp spricht Twitch's
GraphQL-Endpoint intern selbst ab.

### Voraussetzungen

- `yt-dlp` und `ffprobe` müssen im PATH verfügbar sein. `yt-dlp` ist in
  `requirements.txt` aufgeführt; `ffprobe` kommt mit FFmpeg.
- Keine Twitch-App-Registrierung, keine Credentials, kein `.env`-Eintrag
  nötig.

Der Status-Endpunkt `/api/twitch/status` zeigt ehrlich an, ob `yt-dlp`,
`ffprobe` und Schreibrechte im Download-Verzeichnis vorliegen.

### Twitch-Profile

1. Auf `/vod-downloader` oder `/twitch-profiles` ein Profil hinzufügen –
   entweder als Login (`casepayt`) oder als Channel-URL
   (`https://www.twitch.tv/casepayt`).
2. Der Server speichert Login + Channel-URL als JSON unter
   `data/twitch_profiles/<id>.json`. Keine Twitch-API-Credentials nötig;
   Display-Name und Avatar werden bei Bedarf via yt-dlp abgerufen
   (`POST /api/twitch/profiles/{id}/refresh`).
3. Pro Profil können VODs + Clips synchronisiert
   (`POST /api/twitch/profiles/{id}/sync-vods`) oder manuell importiert
   werden (`POST /api/vods/import`).
4. Profile können gelöscht werden. Ein Profil mit angehängten VODs kann
   nicht gelöscht werden (HTTP 409 – erst die VODs löschen).

### VODs und Clips

- Sync ruft die neuesten VODs **und Clips** eines Channels via
  `yt-dlp --flat-playlist` ab (limitiert via `TTVTURBO_VOD_SYNC_LIMIT`,
  Default 100) und legt sie als `DISCOVERED`-Einträge an. Clips werden
  mit `type: "clip"` markiert.
- Manuelles Importieren akzeptiert `twitch.tv/videos/<id>` **und**
  `twitch.tv/<channel>/clip/<slug>` URLs. Channel-URLs und fremde Domains
  werden mit HTTP 400 abgelehnt.
- Jeder VOD durchläuft die Statusmaschine
  `DISCOVERED → QUEUED → DOWNLOADING → VERIFYING → READY`
  (oder `FAILED` / `CANCELED`).
- Downloads laufen über `yt-dlp` als Subprocess. Fortschritt
  (Prozent, Bytes/s, ETA) wird live im Frontend gepollt (2 s Intervall),
  solange VODs in einem transienten Status sind.
- `READY`-VODs zeigen Dateiname, Größe, Auflösung und Codec; die Datei
  kann direkt heruntergeladen werden.
- VODs können abgebrochen (Cancel), erneut gestartet (Retry) oder
  gelöscht werden (inkl. Videodatei).

### Datenspeicher

- Twitch-Profile: `data/twitch_profiles/<id>.json`
- VOD-Metadaten: `data/vods/<id>.json`
- VOD-Videodateien: `data/vods/<id>/source.<ext>` (oder
  konfigurierbar via `TTVTURBO_VOD_DOWNLOAD_DIR`)
- Path-Traversal ist überall blockiert; IDs werden gegen UUIDs validiert.

## API-Endpunkte

| Methode | Pfad                          | Beschreibung                                  |
|---------|-------------------------------|-----------------------------------------------|
| GET     | `/`                           | SPA `index.html`                              |
| GET     | `/api/status`                 | realer System- und Aufnahmestatus             |
| POST    | `/api/recordings`             | empfängt Browseraufnahme, konvertiert zu WAV  |
| GET     | `/api/recordings`             | listet WAVs (neueste zuerst)                  |
| GET     | `/api/recordings/{filename}`  | liefert eine gespeicherte WAV-Datei           |
| DELETE  | `/api/recordings/{filename}`  | löscht eine gespeicherte WAV-Datei            |
| GET     | `/api/voice-clone/status`     | Voice-Clone-Modulstatus (verfügbar, busy, Modell) |
| POST    | `/api/voice-clone/preload-model` | Modell vorab in den Worker-Subprocess laden |
| GET     | `/api/voice-clone/analyze-reference/{filename}` | technische Qualitätsanalyse einer Aufnahme |
| POST    | `/api/voice-clone/generations`| startet eine neue Qwen3-TTS-Generierung       |
| GET     | `/api/voice-clone/generations`| listet alle Generierungen (Metadaten)         |
| GET     | `/api/voice-clone/generations/{id}` | einzelne Generierung (Metadaten)        |
| GET     | `/api/voice-clone/generations/{id}/audio` | WAV-Output einer fertigen Generierung |
| GET     | `/api/voice-clone/generations/{id}/log` | Worker-Log einer Generierung          |
| DELETE  | `/api/voice-clone/generations/{id}` | löscht eine Generierung (Verzeichnis)  |
| GET     | `/api/voice-profiles/scripts` | listet die 88 Aufnahmeskripte des Voice Packs |
| GET     | `/api/voice-profiles`         | listet alle nicht-archivierten Profile        |
| POST    | `/api/voice-profiles`         | erstellt ein neues Profil                     |
| GET     | `/api/voice-profiles/{id}`    | einzelnes Profil mit Referenzen und Fortschritt |
| PATCH   | `/api/voice-profiles/{id}`    | Profil umbenennen oder archivieren/wiederherstellen |
| DELETE  | `/api/voice-profiles/{id}`    | Profil löschen (WAVs bleiben erhalten)        |
| PUT     | `/api/voice-profiles/{id}/references/{script_id}` | Aufnahme als Referenz zuweisen (Server analysiert Qualität) |
| DELETE  | `/api/voice-profiles/{id}/references/{script_id}` | Referenz-Verknüpfung entfernen |
| POST    | `/api/voice-profiles/{id}/references/{script_id}/accept-review` | REVIEW-Referenz explizit akzeptieren |
| GET     | `/api/twitch/status`          | Twitch-Integrationsstatus (yt-dlp, ffprobe, Schreibrechte) |
| GET     | `/api/twitch/profiles`        | listet alle Twitch-Profile (mit VOD-Anzahl)   |
| POST    | `/api/twitch/profiles`        | Twitch-Profil anlegen (Login oder Channel-URL) |
| GET     | `/api/twitch/profiles/{id}`   | einzelnes Twitch-Profil                       |
| POST    | `/api/twitch/profiles/{id}/refresh` | Profil-Metadaten neu abrufen             |
| DELETE  | `/api/twitch/profiles/{id}`   | Twitch-Profil löschen (VODs müssen zuerst entfernt werden) |
| POST    | `/api/twitch/profiles/{id}/sync-vods` | neueste VODs + Clips des Channels synchronisieren |
| GET     | `/api/vods`                   | listet VODs (filterbar nach profile_id, status, search, sort) |
| POST    | `/api/vods/import`            | VOD/Clip manuell importieren (twitch.tv/videos/<id> oder /<channel>/clip/<slug>) |
| GET     | `/api/vods/{id}`              | einzelner VOD (Metadaten + Fortschritt + Download) |
| DELETE  | `/api/vods/{id}`              | VOD löschen (Metadaten + Videodatei)          |
| GET     | `/api/vods/{id}/file`         | VOD-Videodatei herunterladen (READY-VODs)     |
| GET     | `/api/vods/{id}/stream-download` | VOD-Datei als Stream-Response              |
| GET     | `/api/vods/{id}/log`          | Worker-Log eines VOD-Downloads                |
| POST    | `/api/vods/{id}/download`     | Download starten (DISCOVERED/FAILED/CANCELED → QUEUED) |
| POST    | `/api/vods/{id}/cancel`       | laufenden Download abbrechen → CANCELED       |
| POST    | `/api/vods/{id}/retry`        | fehlgeschlagenen Download neu starten         |
| GET     | `/api/transcription/status`   | Transkriptions-Modulstatus                    |
| POST    | `/api/transcription/preload-model` | Whisper-Modell vorab laden               |
| POST    | `/api/transcriptions`         | Transkription eines VODs oder Uploads starten |
| POST    | `/api/transcriptions/upload`  | Datei hochladen und transkribieren            |
| GET     | `/api/transcriptions`         | listet alle Transkriptions-Jobs               |
| GET     | `/api/transcriptions/{id}`    | einzelner Transkriptions-Job                  |
| POST    | `/api/transcriptions/{id}/cancel` | Transkription abbrechen                  |
| POST    | `/api/transcriptions/{id}/retry` | Transkription neu starten                 |
| DELETE  | `/api/transcriptions/{id}`    | Transkriptions-Job löschen                    |
| GET     | `/api/transcriptions/{id}/json` | Transkript als JSON                        |
| GET     | `/api/transcriptions/{id}/txt`  | Transkript als TXT                        |
| GET     | `/api/transcriptions/{id}/srt`  | Transkript als SRT                        |
| GET     | `/api/transcriptions/{id}/vtt`  | Transkript als VTT                        |
| GET     | `/api/vods/{id}/transcriptions` | Transkriptionen eines VODs                  |
| GET     | `/api/vods/{id}/artifacts/audio` | Audio-Artefakte eines VODs                 |
| POST    | `/api/vods/{id}/artifacts/audio` | Audio-Extraktion für einen VOD anstoßen     |
| GET     | `/api/vods/{id}/artifacts/audio/file` | Audio-Artefakt-Datei herunterladen     |
| GET     | `/api/library/uploads`        | listet Library-Uploads                        |
| GET     | `/api/library/uploads/{id}/file` | Library-Upload-Datei herunterladen        |
| DELETE  | `/api/library/uploads/{id}`   | Library-Upload löschen                        |
| GET     | `/api/library/items`          | listet Library-Items (Downloads + Uploads)    |
| GET     | `/api/library/items/{id}`     | einzelnes Library-Item                        |
| GET     | `/api/library/items/{id}/file` | Library-Item-Datei herunterladen             |
| POST    | `/api/library/uploads`        | Datei in die Library hochladen                |
| DELETE  | `/api/library/items/{id}`     | Library-Item löschen                          |
| POST    | `/api/pipeline-runs`          | Pipeline-Run starten (Download → Audio → Transkription) |
| GET     | `/api/pipeline-runs`          | listet alle Pipeline-Runs                     |
| GET     | `/api/pipeline-runs/{id}`     | einzelner Pipeline-Run                        |
| POST    | `/api/pipeline-runs/{id}/cancel` | Pipeline-Run abbrechen                     |
| POST    | `/api/pipeline-runs/{id}/retry` | Pipeline-Run neu starten                    |
| DELETE  | `/api/pipeline-runs/{id}`     | Pipeline-Run löschen                          |
| GET     | `/api/vods/{id}/pipeline-runs` | Pipeline-Runs eines VODs                      |

`/api/status` liefert u. a. App-Version, Laufzeit, Aufnahmeanzahl,
Gesamtdauer, belegten Speicher, freien Speicher und den Featurestatus.
Alle Werte werden real berechnet; es werden keine vollständigen
Serverpfade nach außen gegeben.

## Aufnahmespeicher

Aufnahmen werden als echte WAV-Dateien unter `data/recordings/` gespeichert.
Dateinamen sind zufällige UUIDs. Path-Traversal, versteckte und
temporäre Dateien werden gefiltert. Beschädigte WAVs werden beim Listen
übersprungen und lassen den Server nicht abstürzen.

## Tests

```powershell
# Frontend
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build

# Backend
pytest
```

Alternativ das kombinierte lokale Verifikationsscript ausführen:

```powershell
.\scripts\verify_local.ps1
.\scripts\verify_local.ps1 -IncludeGpuTest   # zusaetzlich GPU-Diagnose
```

Voice-Pack-Validator (prueft `config/voice_lab/scripts/de-DE/ttvturbo_voice_pack_v1.json`
auf Struktur, Eindeutigkeit und Plausibilitaet):

```powershell
python scripts/validate_voice_scripts.py
```

Getestet werden u. a.:

- `/api/status` mit realen Werten
- Aufnahmeanzahl, Gesamtdauer, Gesamtgröße, freier Speicher
- Featurestatus (inkl. `voice_cloning: available`)
- SPA-Auslieferung aus `frontend/dist/` und SPA-Fallback fuer unbekannte Routen
- `/api/*` erhält keinen SPA-Fallback
- beschädigte WAV wird ignoriert
- Path Traversal bleibt blockiert (Aufnahmen und Voice Clone)
- Voice-Clone-Validierung (fehlende Referenz, leerer Text, zu langer Text)
- Voice-Clone-Qualitätsanalyse (REJECT, REVIEW, GOOD)
- Voice-Clone-Subprocess-Fehler werden als FAILED mit Begründung persistiert
- Voice-Clone-Konfliktlock (zweite Anfrage während laufender Generierung → 409)
- Voice-Clone-Restart-Recovery (transiente Status werden FAILED)
- Voice-Clone-Output-Validierung (silent, byte-identisch mit Referenz)
- Voice-Clone-E2E (gated via `TTVTURBO_RUN_QWEN_TTS_E2E=1`, lädt das echte Modell)
- Voice-Profile-CRUD (Erstellen, Lesen, Umbenennen, Archivieren, Löschen)
- Voice-Profile-Referenzen (Zuweisen mit Server-Qualitätsanalyse, Trennen, Review-Akzeptieren)
- Aufnahme-Löschschutz bei Profil-Referenzierung (HTTP 409)
- Voice-Clone-Profilmodus (Generierung aus akzeptierter Profilreferenz)
- VOD-Pipeline: yt-dlp-basierter Channel-Lister (VODs + Clips, keine API-Credentials)
- VOD-Pipeline: Twitch-Profile (Anlegen via Login/URL, Refresh, Löschen mit VOD-Schutz)
- VOD-Pipeline: VOD-Sync (neueste VODs, Limit, Dedup-Verhalten)
- VOD-Pipeline: VOD-Import (twitch.tv/videos/<id> und /<channel>/clip/<slug>, Channel/Fremd-Domain abgelehnt)
- VOD-Pipeline: Download-Worker (yt-dlp-Subprocess, Statusmaschine, Restart-Recovery)
- VOD-Pipeline: API-Integration (Status, Profile, VODs, Download-Start/Cancel/Retry, Datei-Download)
- VOD-Pipeline: App-Integration (Router-Wiring, Status-Payload, Twitch-Status-Endpunkt)
- VOD-Pipeline: Frontend (VOD-Pipeline-Seite, VOD-Downloader-Seite, Twitch-Profile-Seite, VOD-Detail-Seite, Dashboard-Karte, Status-Banner, Profil-Auswahl, VOD-Liste mit Download-Controls, Import-Form, Redirect /vod-explorer → /vod-downloader)
- Transkription: faster-whisper-Subprocess, Statusmaschine, Restart-Recovery, Export (JSON/TXT/SRT/VTT)
- Transkription: API-Integration (Status, Jobs, Cancel/Retry, Formate, VOD-Transkriptionen)
- Media Processing: Audio-Extraktion, Pipeline-Runs (Download → Audio → Transkription)
- Library: persistente Video-Sammlung (Downloads + Uploads), Migration bestehender VODs
- Voice-Script-Pack-Validator (Struktur, Eindeutigkeit, Plausibilität der 88 Skripte)
- Dashboard-, Voice-Lab-, Voice-Clone-Tab-, Voice-Profiles-Tab- und Generierungen-Tab-Tests (Vitest + RTL)

## Bekannte Einschränkungen

- Keine Smartphone-Optimierung (Desktop primero).
- Einstellungen werden nur in `localStorage` gespeichert.
- Keine serverseitige Einstellungsdatenbank, keine Benutzerkonten.
- Voice-Clone-E2E-Test lädt das echte Qwen3-TTS-Modell (gated via
  `TTVTURBO_RUN_QWEN_TTS_E2E=1`); standardmäßig übersprungen, auch in CI.
- Voice Cloning und GPU-Transkription benötigen eine funktionierende
  CUDA-Runtime (`requirements-gpu.txt`); ohne CUDA ist das Basissystem
  lauffähig, aber Generierungen/Transkriptionen werden `FAILED` gemeldet.
- Clip-Suche (Pipeline-Schritt `FIND_CLIPS`, Status `NOT_IMPLEMENTED`) in der VOD Pipeline ist als `NOT_IMPLEMENTED`
  markiert.
- Kein Videoeditor, keine Automationen, kein Publishing.
- Keine Worker, keine Redis, kein Docker, kein Tauri/Rust.

## Status

Real implementiert:

- Browseraufnahme
- FFmpeg-WAV-Konvertierung
- Aufnahmenbibliothek
- Wiedergabe
- Download
- Löschen
- Dashboardstatus (reale Werte)
- React-Dashboard mit Routing, Sidebar, Topbar, Einstellungen
- TanStack Query Cache invalidation
- Voice Clone (Qwen3-TTS) mit Subprocess, Qualitätsanalyse, Status-Polling,
  Restart-Recovery, Konflikt-Lock
- Voice Profiles mit 88 Aufnahmeskripten, geführter Aufnahme, Server-
  Qualitätsanalyse, Fortschritts-Tracking und Profilmodus für Voice Clone
- Voice Profiles-Seite mit Profil- und Referenzverwaltung (früher Voice Lab)
- VOD Downloader: Twitch-Profile, VOD/Clip-Sync via yt-dlp, VOD-Import per
  URL, Download-Worker mit Statusmaschine, Restart-Recovery
- VOD Pipeline: Pipeline-Runs (Download → Audio-Extraktion → Transkription)
  mit Statusmaschine, Restart-Recovery; FIND_CLIPS als NOT_IMPLEMENTED
- VOD-Detailansicht: VOD-Metadaten, Audio-Artefakte, Transkriptionen,
  Pipeline-Runs eines VODs
- Transkription (faster-whisper) mit Subprocess, GPU-Lock, Status-Polling,
  Restart-Recovery, Export als JSON/TXT/SRT/VTT
- Media Processing: Audio-Extraktion aus VODs, Pipeline-Orchestrierung
  (Download → Audio → Transkription) mit Statusmaschine und Restart-Recovery
- Persistente Library: Downloads + Uploads an einem Ort, Migration von
  bestehenden VOD-Downloads via `migrate_to_library.py`
- Vitest- und pytest-Tests

Noch nicht implementiert:

- Clip-Vorschläge (FIND_CLIPS)
- Aufnahmestudio, Synthetic Studio
- Videoeditor, Layout Studio
- Automationen, Publishing
