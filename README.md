# TTVturbo

Browserbasiertes Dashboard für echte Mikrofonaufnahmen. Der Browser
nimmt das Mikrofon auf, der Server konvertiert die Browseraufnahme mit
FFmpeg in eine echte WAV-Datei (PCM 16-bit, 44,1 kHz, mono) und stellt
sie über ein React-Dashboard bereit.

## Architektur

```
TTVturbo/
├── app.py                # FastAPI-Backend: Aufnahmen, Voice Clone, Voice Profiles, VOD Pipeline, Status, SPA-Auslieferung
├── voice_profiles_api.py # Voice-Profile FastAPI-Router + Service-Factory
├── vod_pipeline_api.py   # VOD-Pipeline FastAPI-Router (Twitch-Profile, VODs, Downloads, Status)
├── recordings/           # erzeugte WAV-Dateien (real)
├── voice_profiles/       # Voice-Profile-Kern (Library, Storage, Service, Schemas)
├── voice_profiles_data/  # persistierte Profile (JSON, konfigurierbar via TTVTURBO_VOICE_PROFILES_DIR)
├── vod_pipeline/         # Twitch-VOD-Pipeline-Kern (Schemas, Storage, TwitchClient, Service, Downloader-Worker)
├── ttvturbo_data/        # persistierte Twitch-Profile + VOD-Metadaten (konfigurierbar via TTVTURBO_DATA_DIR)
├── voice_clone/          # Qwen3-TTS Voice-Clone-Modul (Service, Runtime, Qualitätsanalyse, Diagnostics)
├── static/               # Legacy-Testfrontend (Fallback, wenn frontend/dist fehlt)
├── tests/                # pytest-Backendtests
├── scripts/              # verify_local.ps1 - kombinierte lokale Verifikation
├── .github/workflows/    # ci.yml - GPU-freie CI (Python + Frontend + FFmpeg)
├── requirements.txt      # Basissystem (FastAPI, soundfile, numpy, psutil, yt-dlp)
├── requirements-dev.txt  # pytest + httpx
├── requirements-gpu.txt  # NVIDIA-/Qwen-Stack (torch+cu128, qwen-tts, transformers)
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
│       ├── components/   # Layout, UI-Wrappers, Recordings, ErrorBoundary
│       ├── pages/        # Dashboard, VoiceProfiles, VoiceClone, VodPipeline, TwitchProfiles, Settings, NotFound
│       ├── features/     # voiceProfiles + vodPipeline Feature-Module (Schemas, API, Hooks, Panels)
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

## NVIDIA- / Qwen-Unterstützung installieren (optional, RTX 5070)

Diese Stufe ist nur auf der Maschine nötig, die echte Voice-Clone-Generierungen
ausführen soll. Sie installiert die CUDA-fähigen Wheels für den
`voice_clone.runtime`-Worker subprocess. Das FastAPI-Backend selbst importiert
diese Pakete nie; es startet auch ohne sie.

Getestet auf der Zielhardware (siehe `spikes/qwen_tts/REPORT.md`,
Status `REAL_MODEL_VERIFIED`):

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
| `/voice-profiles`   | Voice Profiles       | funktionsfähig               |
| `/vod-pipeline`     | VOD Pipeline         | funktionsfähig (Phase 1)     |
| `/twitch-profiles`  | Twitch-Profile       | funktionsfähig (Phase 1)     |
| `/settings`         | Einstellungen        | teilweise funktionsfähig     |
| `/vod-explorer`     | (Weiterleitung)      | leitet auf `/vod-pipeline`   |
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
6. WAV wird unter `recordings/` gespeichert.
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

Die Voice-Clone-Artefakte liegen unter `voice_clones/<id>/` mit
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

Profile liegen als JSON-Dateien unter `voice_profiles_data/` (konfigurierbar
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

1. Auf `/vod-pipeline` oder `/twitch-profiles` ein Profil hinzufügen –
   entweder als Login (`casepayt`) oder als Channel-URL
   (`https://www.twitch.tv/casepayt`).
2. Der Server speichert Login + Channel-URL als JSON unter
   `ttvturbo_data/twitch_profiles/<id>.json`. Keine externe API-Auflösung,
   keine Metadaten von Twitch.
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

- Twitch-Profile: `ttvturbo_data/twitch_profiles/<id>.json`
- VOD-Metadaten: `ttvturbo_data/vods/<id>.json`
- VOD-Videodateien: `ttvturbo_data/vods/<id>/source.<ext>` (oder
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
| GET     | `/api/voice-clone/analyze-reference/{filename}` | technische Qualitätsanalyse einer Aufnahme |
| POST    | `/api/voice-clone/generations`| startet eine neue Qwen3-TTS-Generierung       |
| GET     | `/api/voice-clone/generations`| listet alle Generierungen (Metadaten)         |
| GET     | `/api/voice-clone/generations/{id}` | einzelne Generierung (Metadaten)        |
| GET     | `/api/voice-clone/generations/{id}/audio` | WAV-Output einer fertigen Generierung |
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
| GET     | `/api/twitch/status`          | Twitch-Integrationsstatus (Credentials, API, Downloader, ffprobe) |
| GET     | `/api/twitch/profiles`        | listet alle Twitch-Profile (mit VOD-Anzahl)   |
| POST    | `/api/twitch/profiles`        | Twitch-Profil anlegen (Login oder Channel-URL) |
| DELETE  | `/api/twitch/profiles/{id}`   | Twitch-Profil löschen (VODs müssen zuerst entfernt werden) |
| POST    | `/api/twitch/profiles/{id}/refresh` | Profil-Metadaten von Twitch neu abrufen |
| POST    | `/api/twitch/profiles/{id}/sync-vods` | neueste VODs des Channels synchronisieren |
| GET     | `/api/vods`                   | listet VODs (filterbar nach profile_id, status, search, sort) |
| POST    | `/api/vods/import`            | VOD manuell importieren (nur twitch.tv/videos/<id>) |
| GET     | `/api/vods/{id}`              | einzelner VOD (Metadaten + Fortschritt + Download) |
| DELETE  | `/api/vods/{id}`              | VOD löschen (Metadaten + Videodatei)          |
| GET     | `/api/vods/{id}/file`         | VOD-Videodatei herunterladen (READY-VODs)     |
| POST    | `/api/vods/{id}/download`     | Download starten (DISCOVERED/FAILED/CANCELED → QUEUED) |
| POST    | `/api/vods/{id}/cancel`       | laufenden Download abbrechen → CANCELED       |
| POST    | `/api/vods/{id}/retry`        | fehlgeschlagenen Download neu starten         |

`/api/status` liefert u. a. App-Version, Laufzeit, Aufnahmeanzahl,
Gesamtdauer, belegten Speicher, freien Speicher und den Featurestatus.
Alle Werte werden real berechnet; es werden keine vollständigen
Serverpfade nach außen gegeben.

## Aufnahmespeicher

Aufnahmen werden als echte WAV-Dateien unter `recordings/` gespeichert.
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

Getestet werden u. a.:

- `/api/status` mit realen Werten
- Aufnahmeanzahl, Gesamtdauer, Gesamtgröße, freier Speicher
- Featurestatus (inkl. `voice_cloning: available`)
- statische Frontenddateien und SPA-Fallback
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
- VOD-Pipeline: VOD-Import (nur twitch.tv/videos/<id>, Channel/Clip/Fremd-Domain abgelehnt)
- VOD-Pipeline: Download-Worker (yt-dlp-Subprocess, Statusmaschine, Restart-Recovery)
- VOD-Pipeline: API-Integration (Status, Profile, VODs, Download-Start/Cancel/Retry, Datei-Download)
- VOD-Pipeline: App-Integration (Router-Wiring, Status-Payload, Twitch-Status-Endpunkt)
- VOD-Pipeline: Frontend (VOD-Pipeline-Seite, Twitch-Profile-Seite, Dashboard-Karte, Status-Banner, Profil-Auswahl, VOD-Liste mit Download-Controls, Import-Form, Redirect /vod-explorer → /vod-pipeline)
- Dashboard-, Voice-Lab-, Voice-Clone-Tab-, Voice-Profiles-Tab- und Generierungen-Tab-Tests (Vitest + RTL)

## Bekannte Einschränkungen

- Keine Smartphone-Optimierung (Desktop primero).
- Einstellungen werden nur in `localStorage` gespeichert.
- Keine serverseitige Einstellungsdatenbank, keine Benutzerkonten.
- Voice-Clone-E2E-Test lädt das echte Qwen3-TTS-Modell (gated via
  `TTVTURBO_RUN_QWEN_TTS_E2E=1`); standardmäßig übersprungen, auch in CI.
- Voice Cloning benötigt eine funktionierende CUDA-Runtime
  (`requirements-gpu.txt`); ohne CUDA ist das Basissystem lauffähig, aber
  Generierungen werden `FAILED` gemeldet.
- Kein VOD-Download, kein Twitch- oder OBS-Anschluss.
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
- Vitest- und pytest-Tests

Noch nicht implementiert:

- Transkription
- VOD-Verarbeitung
- Videoeditor
- Publishing
