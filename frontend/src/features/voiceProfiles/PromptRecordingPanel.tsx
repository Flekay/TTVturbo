import { useMemo, useState } from "react";
import { Mic, PlusCircle, Trash2, CheckCircle2, AlertTriangle } from "lucide-react";
import { useRecordingsQuery } from "../../hooks/useQueries";
import { Button } from "../../components/ui/Button";
import { ReferenceStatusBadge } from "./ReferenceStatus";
import type {
  PromptRecordingRequest,
  VoiceProfile,
  VoiceProfileReference,
  VoiceScript,
} from "./types";

interface PromptRecordingPanelProps {
  profile: VoiceProfile;
  script: VoiceScript;
  reference: VoiceProfileReference | null;
  onStartPromptRecording?: (request: PromptRecordingRequest) => void;
  onAttachRecording: (recordingFilename: string) => void;
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

function audioUrlFor(
  filename: string,
  recordings: { filename: string; audio_url: string }[],
): string {
  const found = recordings.find((r) => r.filename === filename);
  return found?.audio_url ?? `/api/recordings/${encodeURIComponent(filename)}`;
}

export function PromptRecordingPanel({
  profile,
  script,
  reference,
  onStartPromptRecording,
  onAttachRecording,
  onDetachReference,
  onAcceptReview,
  attachPending,
  detachPending,
  acceptPending,
}: PromptRecordingPanelProps) {
  const recordingsQuery = useRecordingsQuery();
  const recordings = recordingsQuery.data?.recordings ?? [];
  const [pickerOpen, setPickerOpen] = useState(false);

  const audioUrl = useMemo(
    () => (reference ? audioUrlFor(reference.recording_filename, recordings) : null),
    [reference, recordings],
  );

  const recommendedDuration = script.recommended_duration_seconds ?? null;
  const tag = script.style ?? script.category ?? "";
  const recordingAvailable = recordings.length > 0;
  const canRecord = !!onStartPromptRecording;
  const status = reference?.status ?? null;
  const isReview = status === "REVIEW";
  const hasReference = !!reference;
  const warnings = referenceWarnings(reference);
  const rejectionReasons = status === "REJECTED" ? referenceReasons(reference) : [];

  const handleRecord = () => {
    if (!onStartPromptRecording) return;
    onStartPromptRecording({
      profileId: profile.id,
      scriptId: script.id,
      scriptText: script.text,
    });
  };

  return (
    <div className="vp-prompt-panel" aria-label="Prompt-Aufnahme-Panel">
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
          <p className="page__description">Noch keine Aufnahme verknüpft.</p>
        )}
      </div>

      <div className="vp-prompt-panel__actions">
        <Button
          variant="primary"
          size="sm"
          onClick={handleRecord}
          disabled={!canRecord}
          title={canRecord ? undefined : "Kein Recorder angeschlossen"}
        >
          <Mic size={14} /> Jetzt aufnehmen
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            if (recordingAvailable) setPickerOpen((v) => !v);
          }}
          disabled={!recordingAvailable || attachPending}
          title={recordingAvailable ? undefined : "Keine Aufnahmen vorhanden"}
        >
          <PlusCircle size={14} /> Vorhandene Aufnahme zuweisen
        </Button>
        {hasReference && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onDetachReference}
            loading={detachPending}
            disabled={detachPending}
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
            disabled={acceptPending}
          >
            <CheckCircle2 size={14} /> Review ausdrücklich akzeptieren
          </Button>
        )}
      </div>

      {!canRecord && (
        <p className="page__description" role="note">
          Aufnahme nicht verfügbar: kein Recorder angeschlossen. Bestehende
          Aufnahmen können weiterhin manuell verknüpft werden.
        </p>
      )}

      {pickerOpen && recordingAvailable && (
        <div className="vp-recording-picker" role="listbox" aria-label="Aufnahmen auswählen">
          {recordings.map((rec) => (
            <button
              key={rec.filename}
              type="button"
              className="vp-recording-picker__item"
              onClick={() => {
                onAttachRecording(rec.filename);
                setPickerOpen(false);
              }}
              disabled={attachPending}
              aria-label={`Aufnahme ${rec.filename} zuweisen`}
            >
              <span>
                <span className="vp-recording-picker__filename">{rec.filename}</span>
                <br />
                <span className="vp-recording-picker__info">
                  {new Date(rec.created_at).toLocaleString()} ·{" "}
                  {formatDuration(rec.duration_seconds)}
                </span>
              </span>
              <audio
                controls
                preload="none"
                src={rec.audio_url}
                aria-label={`Vorschau für ${rec.filename}`}
                style={{ maxWidth: 220 }}
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
