import { useEffect, useMemo, useState } from "react";
import { Mic, Square, RotateCcw, Trash2, CheckCircle2, AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { useRecorder } from "../../hooks/useRecorder";
import { ReferenceStatusBadge } from "./ReferenceStatus";
import type {
  VoiceProfileReference,
  VoiceScript,
} from "./types";

interface PromptRecordingPanelProps {
  script: VoiceScript;
  reference: VoiceProfileReference | null;
  profileId: string;
  onAttachReference: (recordingFilename: string) => void;
  onDetachReference: () => void;
  onAcceptReview: () => void;
  attachPending: boolean;
  detachPending: boolean;
  acceptPending: boolean;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatDurationRange(
  range: { min: number; max: number } | null | undefined,
): string {
  if (!range) return "?";
  return `${formatDuration(range.min)}–${formatDuration(range.max)}`;
}

/** Extract the quality warnings from the analyzer result stored on the reference. */
function referenceWarnings(reference: VoiceProfileReference | null): string[] {
  if (!reference) return [];
  const vcr = reference.quality?.voice_clone_reference as
    | { warnings?: unknown }
    | undefined;
  if (vcr && Array.isArray(vcr.warnings)) return vcr.warnings as string[];
  return [];
}

/** Extract the quality reasons from the analyzer result stored on the reference. */
function referenceReasons(reference: VoiceProfileReference | null): string[] {
  if (!reference) return [];
  const vcr = reference.quality?.voice_clone_reference as
    | { reasons?: unknown }
    | undefined;
  if (vcr && Array.isArray(vcr.reasons)) return vcr.reasons as string[];
  return [];
}

function audioUrlFor(filename: string): string {
  return `/api/recordings/${encodeURIComponent(filename)}`;
}

export function PromptRecordingPanel({
  script,
  reference,
  profileId,
  onAttachReference,
  onDetachReference,
  onAcceptReview,
  attachPending,
  detachPending,
  acceptPending,
}: PromptRecordingPanelProps) {
  const audioUrl = useMemo(
    () => (reference ? audioUrlFor(reference.recording_filename) : null),
    [reference],
  );

  const recommendedDuration = script.recommended_duration_seconds ?? null;
  const tag = script.style ?? script.category ?? "";
  const status = reference?.status ?? null;
  const isReview = status === "REVIEW";
  const hasReference = !!reference;
  const warnings = referenceWarnings(reference);
  const rejectionReasons = status === "REJECTED" ? referenceReasons(reference) : [];

  const [recorderOpen, setRecorderOpen] = useState(false);

  // When a recording finishes uploading, immediately attach it to the
  // current script (replacing any existing reference). The backend's
  // PUT endpoint is idempotent and runs the real quality analyzer.
  const recorder = useRecorder({
    onUploaded: (filename) => {
      onAttachReference(filename);
      setRecorderOpen(false);
    },
    onUploadError: () => {
      // Toast is already surfaced by the attach handler / recorder state.
    },
  });

  // Reset the recorder whenever the user closes the inline panel or switches
  // to a different script (component remounts on script change anyway, but
  // being explicit avoids lingering state across toggles).
  useEffect(() => {
    if (!recorderOpen && recorder.state !== "idle") {
      recorder.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recorderOpen]);

  const recorderBusy =
    recorder.state === "uploading" ||
    recorder.state === "requesting_permission" ||
    recorder.state === "recording" ||
    recorder.state === "converting" ||
    attachPending;

  const statusLabel = (() => {
    switch (recorder.state) {
      case "idle":
        return "Bereit. Mikrofon aktivieren, um zu starten.";
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
        return "Aufnahme gespeichert und verknüpft.";
      case "error":
        return "Fehler.";
    }
  })();

  return (
    <div className="vp-prompt-panel" aria-label="Prompt-Referenz-Panel">
      <div className="vp-prompt-panel__section">
        <div className="vp-prompt-panel__meta">
          {tag && <span>Stil/Kategorie: {tag}</span>}
          {recommendedDuration !== null && (
            <span>Empfohlene Dauer: {formatDurationRange(recommendedDuration)}</span>
          )}
          <span>
            Status: <ReferenceStatusBadge status={status} />
          </span>
        </div>
        <p className="vp-prompt-panel__text">{script.text}</p>
        {script.recording_notes && (
          <p className="vp-prompt-panel__notes" aria-label="Aufnahmehinweise">
            Hinweise: {script.recording_notes}
          </p>
        )}
      </div>

      <div className="vp-prompt-panel__section">
        <div className="vp-prompt-panel__section-title">Aktuelle Referenz</div>
        {hasReference ? (
          <>
            <div className="vp-prompt-panel__meta">
              <span>Datei: {reference?.recording_filename}</span>
              {reference?.attached_at && (
                <span>Verknüpft: {new Date(reference.attached_at).toLocaleString()}</span>
              )}
              {reference?.quality_class && (
                <span>Qualitätsklasse: {reference.quality_class}</span>
              )}
            </div>
            {audioUrl && (
              <audio
                controls
                preload="none"
                src={audioUrl}
                aria-label={`Audio-Player für ${reference?.recording_filename}`}
              />
            )}
            {warnings.length > 0 && (
              <ul className="vp-prompt-panel__warnings" role="note">
                {warnings.map((w, i) => (
                  <li key={i}>
                    <AlertTriangle size={12} /> {w}
                  </li>
                ))}
              </ul>
            )}
            {rejectionReasons.length > 0 && (
              <ul className="vp-prompt-panel__rejections" role="note">
                {rejectionReasons.map((r, i) => (
                  <li key={i}>
                    <AlertTriangle size={12} /> {r}
                  </li>
                ))}
              </ul>
            )}
            {reference?.quality && Object.keys(reference.quality).length > 0 && (
              <details className="vp-prompt-panel__technical" aria-label="Technische Qualität">
                <summary>Technische Qualität anzeigen</summary>
                <pre>{JSON.stringify(reference.quality, null, 2)}</pre>
              </details>
            )}
          </>
        ) : (
          <p className="page__description">Noch keine Referenz verknüpft.</p>
        )}
      </div>

      <div className="vp-prompt-panel__actions">
        {!recorderOpen ? (
          <Button
            variant="primary"
            size="sm"
            onClick={() => setRecorderOpen(true)}
            disabled={recorderBusy}
          >
            {hasReference ? (
              <>
                <RefreshCw size={14} /> Ersetzen / Neu aufnehmen
              </>
            ) : (
              <>
                <Mic size={14} /> Aufnehmen
              </>
            )}
          </Button>
        ) : null}
        {hasReference && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onDetachReference}
            loading={detachPending}
            disabled={detachPending || recorderBusy}
          >
            <Trash2 size={14} /> Verknüpfung entfernen
          </Button>
        )}
        {isReview && (
          <Button
            variant="primary"
            size="sm"
            onClick={onAcceptReview}
            loading={acceptPending}
            disabled={acceptPending || recorderBusy}
          >
            <CheckCircle2 size={14} /> Review ausdrücklich akzeptieren
          </Button>
        )}
        {recorderOpen && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setRecorderOpen(false)}
            disabled={recorderBusy}
          >
            Abbrechen
          </Button>
        )}
      </div>

      {recorderOpen && (
        <div className="vp-prompt-panel__recorder" aria-label="Inline Audio-Recorder">
          <div className="recorder__status" role="status" aria-live="polite">
            <span>{statusLabel}</span>
          </div>
          {recorder.error && (
            <div className="state state--error" role="alert" style={{ padding: "12px 16px" }}>
              <AlertTriangle size={16} />
              <div>{recorder.error.message}</div>
              <Button variant="secondary" size="sm" onClick={recorder.reset}>
                <RotateCcw size={14} /> Zurücksetzen
              </Button>
            </div>
          )}
          <div className="recorder__controls">
            {recorder.state === "idle" || recorder.state === "error" ? (
              <Button
                variant="primary"
                size="sm"
                onClick={recorder.requestPermission}
                disabled={recorderBusy}
              >
                <Mic size={14} /> Mikrofon aktivieren
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                onClick={recorder.start}
                disabled={recorder.state === "recording" || recorderBusy}
                loading={recorder.state === "requesting_permission"}
              >
                <Mic size={14} /> Aufnahme starten
              </Button>
            )}
            <Button
              variant="danger"
              size="sm"
              onClick={recorder.stop}
              disabled={recorder.state !== "recording"}
            >
              <Square size={14} /> Aufnahme stoppen
            </Button>
            <div className="recorder__duration" aria-label="Aufnahmedauer">
              {formatDuration(recorder.durationSeconds)}
            </div>
          </div>
          {/* profileId is implicit through the attach handler; referenced here
              so the dependency is explicit and the linter stays calm. */}
          <span className="sr-only" aria-hidden="true">{profileId}</span>
        </div>
      )}
    </div>
  );
}
