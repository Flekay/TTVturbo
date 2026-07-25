# TTVturbo

Minimale lokale Browser-Anwendung für echte Mikrofonaufnahmen.
Der Browser nimmt das Mikrofon auf, der Server konvertiert die
Browser-Aufnahme mit FFmpeg in eine echte WAV-Datei (PCM 16-bit,
44,1 kHz, mono) und stellt sie zum Abspielen bereit.

## Struktur

```
TTVturbo/
├── app.py              # FastAPI + Uvicorn + FFmpeg-Konvertierung
├── static/
│   ├── index.html      # UI
│   ├── app.js          # MediaRecorder + Upload + Player
│   └── style.css
├── recordings/         # erzeugte WAV-Dateien
├── requirements.txt
└── README.md
```

## Voraussetzungen

- Python 3.10+
- FFmpeg (und ffprobe) im PATH
- Ein Browser mit `MediaRecorder` und `getUserMedia` (Chrome, Edge, Firefox)

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

FFmpeg unter Windows z. B. mit:

```powershell
winget install --id=Gyan.FFmpeg -e
```

## Start

```powershell
python app.py
```

Der Server läuft auf `http://127.0.0.1:8000`.

Im Browser `http://127.0.0.1:8000` öffnen, Mikrofon erlauben,
"Aufnahme starten" / "Aufnahme stoppen" verwenden. Die erzeugte
WAV-Datei wird unter `recordings/` gespeichert und im Player
angezeigt.

## Endpunkte

| Methode | Pfad                          | Beschreibung                                  |
|---------|-------------------------------|-----------------------------------------------|
| GET     | `/`                           | liefert `static/index.html`                   |
| POST    | `/api/recordings`             | empfängt die Browseraufnahme, konvertiert WAV |
| GET     | `/api/recordings/{filename}`  | liefert eine gespeicherte WAV-Datei           |

## Automatisierte Prüfungen

Siehe Abschnitt "Verifizierung" unten (per Skript ausführbar).
Die echten Mikrofontests im Browser müssen manuell durchgeführt
werden, sofern kein Mikrofon verfügbar ist (siehe Status im
Verifizierungsabschnitt).

## Hinweise

- Es wird das vom Browser unterstützte Aufnahmeformat verwendet
  (typisch `audio/webm;codecs=opus`).
- Es gibt keine Datenbank, kein Login, keine Worker, keine
  zusätzlichen Module.
