import { useEffect, useMemo, useState } from "react";
import { Mic, Square, AlertCircle, RotateCcw } from "lucide-react";
import { useRecorder } from "../../hooks/useRecorder";
import { useUIStore } from "../../stores/uiStore";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

interface AudioRecorderProps {
  onUploaded?: (filename: string) => void;
  onUploadError?: (message: string) => void;
}

export function AudioRecorder({ onUploaded, onUploadError }: AudioRecorderProps) {
  const autoplay = useUIStore((s) => s.autoplayAfterRecord);
  const [lastUploadedUrl, setLastUploadedUrl] = useState<string | null>(null);

  const recorder = useRecorder({
    onUploaded: (filename) => {
      const url = `/api/recordings/${encodeURIComponent(filename)}?t=${Date.now()}`;
      setLastUploadedUrl(url);
      onUploaded?.(filename);
    },
    onUploadError,
  });

  useEffect(() => {
    if (recorder.state === "completed" && lastUploadedUrl && autoplay) {
      const audio = document.querySelector<HTMLAudioElement>("audio.recorder__preview");
      if (audio) {
        audio.src = lastUploadedUrl;
        void audio.play().catch(() => undefined);
      }
    }
  }, [recorder.state, lastUploadedUrl, autoplay]);

  const statusLabel = useMemo(() => {
    switch (recorder.state) {
      case "idle":
        return "Bereit. Mikrofonzugriff anfragen, um zu starten.";
      case "requesting_permission":
        return "Mikrofonberechtigung wird angefragt …";
      case "ready":
        return "Mikrofon bereit. Aufnahme starten.";
      case "recording":
        return "Aufnahme läuft …";
      case "uploading":
        return "Lade hoch und konvertiere mit FFmpeg …";
      case "converting":
        return "Konvertiere mit FFmpeg …";
      case "completed":
        return "Aufnahme gespeichert und abspielbar.";
      case "error":
        return "Fehler.";
    }
  }, [recorder.state]);

  const statusDotClass =
    recorder.state === "recording"
      ? "is-recording"
      : recorder.state === "ready" || recorder.state === "completed"
        ? "is-ready"
        : recorder.state === "uploading" || recorder.state === "converting" || recorder.state === "requesting_permission"
          ? "is-busy"
          : "";

  const isBusy =
    recorder.state === "uploading" ||
    recorder.state === "requesting_permission" ||
    recorder.state === "recording";

  // Warn the user before navigating away during an active recording.
  useEffect(() => {
    if (recorder.state !== "recording") return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [recorder.state]);

  const bars = 24;
  const levelHeight = Math.max(4, Math.min(100, recorder.level * 100));

  return (
    <section className="recorder" aria-label="Audio-Recorder">
      <div className="recorder__header">
        <div className="recorder__status" role="status" aria-live="polite">
          <span className={`recorder__status-dot ${statusDotClass}`} aria-hidden="true" />
          <span>{statusLabel}</span>
        </div>
        {recorder.state === "recording" && (
          <Badge variant="error" title="Aufnahme aktiv">
            REC
          </Badge>
        )}
      </div>

      {recorder.error && (
        <div className="state state--error" role="alert" style={{ padding: "12px 16px" }}>
          <AlertCircle size={18} />
          <div>{recorder.error.message}</div>
          <Button variant="secondary" size="sm" onClick={recorder.reset}>
            <RotateCcw size={14} /> Zurücksetzen
          </Button>
        </div>
      )}

      <div className="recorder__controls">
        {recorder.state === "idle" || recorder.state === "error" ? (
          <Button variant="primary" onClick={recorder.requestPermission} disabled={isBusy}>
            <Mic size={16} /> Mikrofon aktivieren
          </Button>
        ) : (
          <Button
            variant="primary"
            onClick={recorder.start}
            disabled={recorder.state === "recording" || isBusy}
            loading={recorder.state === "requesting_permission"}
          >
            <Mic size={16} /> Aufnahme starten
          </Button>
        )}

        <Button
          variant="danger"
          onClick={recorder.stop}
          disabled={recorder.state !== "recording"}
        >
          <Square size={16} /> Aufnahme stoppen
        </Button>

        <div className="recorder__duration" aria-label="Aufnahmedauer">
          {formatDuration(recorder.durationSeconds)}
        </div>

        <div
          className="recorder__level"
          aria-hidden="true"
          title="Eingangspegel"
        >
          {Array.from({ length: bars }).map((_, i) => {
            // Distribute the peak across bars for a simple meter.
            const threshold = (i + 1) / bars;
            const active = recorder.level >= threshold * 0.9;
            const height = active ? levelHeight : 4;
            return (
              <div
                key={i}
                className="recorder__level-bar"
                style={{
                  height: `${height}%`,
                  backgroundColor: active
                    ? i > bars * 0.8
                      ? "var(--color-error)"
                      : i > bars * 0.6
                        ? "var(--color-warning)"
                        : "var(--color-success)"
                    : "var(--color-bg-elevated)",
                }}
              />
            );
          })}
        </div>
      </div>

      {recorder.devices.length > 0 && (
        <div className="recorder__device-select">
          <label htmlFor="recorder-device">Mikrofon</label>
          <select
            id="recorder-device"
            value={recorder.selectedDeviceId}
            onChange={(e) => recorder.selectDevice(e.target.value)}
            disabled={recorder.state === "recording"}
          >
            {recorder.devices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {!recorder.hasMediaRecorder && (
        <div className="state state--error" role="alert">
          <AlertCircle />
          <div className="state__title">MediaRecorder nicht unterstützt</div>
          <div className="state__description">
            Bitte einen aktuellen Chrome, Edge oder Firefox verwenden.
          </div>
        </div>
      )}

      {recorder.state === "completed" && lastUploadedUrl && (
        <div>
          <h3 style={{ fontSize: 14, marginBottom: 8 }}>Neueste Aufnahme</h3>
          <audio
            className="recorder__preview"
            controls
            src={lastUploadedUrl}
            style={{ width: "100%" }}
          />
        </div>
      )}
    </section>
  );
}
