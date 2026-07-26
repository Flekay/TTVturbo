# TTVturbo

Browserbasiertes Dashboard für echte Mikrofonaufnahmen. Der Browser
nimmt das Mikrofon auf, der Server konvertiert die Browseraufnahme mit
FFmpeg in eine echte WAV-Datei (PCM 16-bit, 44,1 kHz, mono) und stellt
sie über ein React-Dashboard bereit.

## Architektur

```
TTVturbo/
├── app.py                # FastAPI-Backend: Aufnahmen, Status, SPA-Auslieferung
├── recordings/           # erzeugte WAV-Dateien (real)
├── static/               # Legacy-Testfrontend (Fallback, wenn frontend/dist fehlt)
├── tests/                # pytest-Backendtests
├── requirements.txt
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
│       ├── pages/        # Dashboard, VoiceLab, Settings, Unavailable, NotFound
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

- Python 3.10+
- Node.js 20+ und npm
- FFmpeg (und ffprobe) im PATH
- Ein Browser mit `MediaRecorder` und `getUserMedia` (Chrome, Edge, Firefox)

## Backendinstallation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

FFmpeg unter Windows z. B. mit:

```powershell
winget install --id=Gyan.FFmpeg -e
```

Für die Backendtests zusätzlich:

```powershell
pip install pytest httpx
```

## Frontendinstallation

```powershell
cd frontend
npm install
```

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
| `/voice-lab`        | Voice Lab            | funktionsfähig               |
| `/settings`         | Einstellungen        | teilweise funktionsfähig     |
| `/vod-explorer`     | VOD Explorer         | noch nicht implementiert     |
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

## Voice-Lab-Funktion

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

## API-Endpunkte

| Methode | Pfad                          | Beschreibung                                  |
|---------|-------------------------------|-----------------------------------------------|
| GET     | `/`                           | SPA `index.html`                              |
| GET     | `/api/status`                 | realer System- und Aufnahmestatus             |
| POST    | `/api/recordings`             | empfängt Browseraufnahme, konvertiert zu WAV  |
| GET     | `/api/recordings`             | listet WAVs (neueste zuerst)                  |
| GET     | `/api/recordings/{filename}`  | liefert eine gespeicherte WAV-Datei           |
| DELETE  | `/api/recordings/{filename}`  | löscht eine gespeicherte WAV-Datei            |

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

Getestet werden u. a.:

- `/api/status` mit realen Werten
- Aufnahmeanzahl, Gesamtdauer, Gesamtgröße, freier Speicher
- Featurestatus
- statische Frontenddateien und SPA-Fallback
- `/api/*` erhält keinen SPA-Fallback
- beschädigte WAV wird ignoriert
- Path Traversal bleibt blockiert
- Dashboard-, Voice-Lab-, Routing- und Löschen-Tests (Vitest + RTL)

## Bekannte Einschränkungen

- Keine Smartphone-Optimierung (Desktop primero).
- Einstellungen werden nur in `localStorage` gespeichert.
- Keine serverseitige Einstellungsdatenbank, keine Benutzerkonten.
- Kein Voice-Cloning, keine TTS, keine Transkription.
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
- Vitest- und pytest-Tests

Noch nicht implementiert:

- Voice-Cloning
- Transkription
- VOD-Verarbeitung
- Videoeditor
- Publishing
