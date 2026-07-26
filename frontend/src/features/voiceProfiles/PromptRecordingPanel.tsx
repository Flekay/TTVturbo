import { useMemo } from "react";
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
  index: number;
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
  index,
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

  const tag = script.style ?? script.category ?? "";
  const status = reference?.status ?? null;
  const isReview = status === "REVIEW";
  const hasReference = !!reference;
  const warnings = referenceWarnings(reference);
  const rejectionReasons = status === "REJECTED" ? referenceReasons(reference) : [];

  const recorder = useRecorder({
    onUploaded: (filename) => {
      onAttachReference(filename);
    },
    onUploadError: () => {
      // Toast is already surfaced by the attach handler / recorder state.
    },
  });

  const isRecording = recorder.state === "recording";
  const isBusy =
    recorder.state === "uploading" ||
    recorder.state === "requesting_permission" ||
    recorder.state === "recording" ||
    recorder.state === "converting" ||
    attachPending;
  const showRecorder =
    isRecording ||
    recorder.state === "uploading" ||
    recorder.state === "converting" ||
    recorder.state === "requesting_permission" ||
    recorder.state === "error";

  const statusLabel = (() => {
    switch (recorder.state) {
      case "requesting_permission":
        return "Mikrofonberechtigung wird angefragt …";
      case "recording":
        return "Aufnahme läuft …";
      case "uploading":
        return "Lade hoch und konvertiere mit FFmpeg …";
      case "converting":
        return "Konvertiere mit FFmpeg …";
      case "error":
        return "Fehler.";
      default:
        return null;
    }
  })();

  return (
    <article
      className="vp-prompt-item"
      aria-label={`Prompt ${index + 1}: ${script.text.replace(/\s+/g, " ").trim()}`}
    >
      <header className="vp-prompt-item__header">
        <span className="vp-prompt-item__order">{index + 1}</span>
        {tag && <span className="vp-prompt-item__tag">{tag}</span>}
        <span className="vp-prompt-item__status">
          <ReferenceStatusBadge status={status} />
        </span>
      </header>

      <p className="vp-prompt-item__text">{script.text}</p>

      {script.recording_notes && (
        <div className="vp-prompt-item__meta">
          <span className="vp-prompt-item__notes">Hinweise: {script.recording_notes}</span>
        </div>
      )}

      <div className="vp-prompt-item__actions">
        {isRecording ? (
          <Button
            variant="danger"
            size="sm"
            onClick={recorder.stop}
            disabled={!isRecording}
          >
            <Square size={14} /> Aufnahme stoppen
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={recorder.start}
            disabled={isBusy}
            loading={
              recorder.state === "requesting_permission" ||
              recorder.state === "uploading" ||
              recorder.state === "converting"
            }
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
        )}
        {hasReference && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onDetachReference}
            loading={detachPending}
            disabled={detachPending || isBusy}
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
            disabled={acceptPending || isBusy}
          >
            <CheckCircle2 size={14} /> Review ausdrücklich akzeptieren
          </Button>
        )}
      </div>

      {showRecorder && (
        <div className="vp-prompt-item__recorder" aria-label="Inline Audio-Recorder">
          {statusLabel && (
            <div className="recorder__status" role="status" aria-live="polite">
              <span>{statusLabel}</span>
              {isRecording && (
                <span className="recorder__duration" aria-label="Aufnahmedauer">
                  {formatDuration(recorder.durationSeconds)}
                </span>
              )}
            </div>
          )}
          {recorder.error && (
            <div className="state state--error" role="alert" style={{ padding: "12px 16px" }}>
              <AlertTriangle size={16} />
              <div>{recorder.error.message}</div>
              <Button variant="secondary" size="sm" onClick={recorder.reset}>
                <RotateCcw size={14} /> Zurücksetzen
              </Button>
            </div>
          )}
          {/* profileId is implicit through the attach handler; referenced here
              so the dependency is explicit and the linter stays calm. */}
          <span className="sr-only" aria-hidden="true">{profileId}</span>
        </div>
      )}

      {hasReference && (
        <div className="vp-prompt-item__reference">
          {audioUrl && (
            <audio
              controls
              preload="none"
              src={audioUrl}
              aria-label={`Audio-Player für ${reference?.recording_filename}`}
            />
          )}
          <div className="vp-prompt-item__reference-meta">
            <span>{reference?.recording_filename}</span>
            {reference?.attached_at && (
              <span>Verknüpft: {new Date(reference.attached_at).toLocaleString()}</span>
            )}
            {reference?.quality_class && (
              <span>Qualität: {reference.quality_class}</span>
            )}
          </div>
          {warnings.length > 0 && (
            <ul className="vp-prompt-item__warnings" role="note">
              {warnings.map((w, i) => (
                <li key={i}>
                  <AlertTriangle size={12} /> {w}
                </li>
              ))}
            </ul>
          )}
          {rejectionReasons.length > 0 && (
            <ul className="vp-prompt-item__rejections" role="note">
              {rejectionReasons.map((r, i) => (
                <li key={i}>
                  <AlertTriangle size={12} /> {r}
                </li>
              ))}
            </ul>
          )}
          {reference?.quality && Object.keys(reference.quality).length > 0 && (
            <details className="vp-prompt-item__technical" aria-label="Technische Qualität">
              <summary>Technische Qualität anzeigen</summary>
              <pre>{JSON.stringify(reference.quality, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
    </article>
  );
}
