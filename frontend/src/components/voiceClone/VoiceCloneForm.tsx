import { useEffect, useMemo, useState } from "react";
import { Wand2, AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { useRecordingsQuery } from "../../hooks/useQueries";
import {
  useCreateGenerationMutation,
  useReferenceQualityQuery,
  useVoiceCloneStatusQuery,
} from "../../hooks/useVoiceClone";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { useToast } from "../ui/ToastProvider";
import type { QualityClass } from "../../types/voiceClone";

const MAX_TARGET_CHARS = 300;

const QUALITY_BADGE: Record<QualityClass, { variant: "success" | "warning" | "error" | "muted"; label: string }> = {
  EXCELLENT: { variant: "success", label: "Exzellent" },
  GOOD: { variant: "success", label: "Gut" },
  REVIEW: { variant: "warning", label: "Review" },
  REJECT: { variant: "error", label: "Reject" },
};

const PHASE_LABELS: Record<string, string> = {
  QUEUED: "Warteschlange",
  VALIDATING_REFERENCE: "Referenz wird geprüft",
  LOADING_MODEL: "Modell wird geladen",
  GENERATING: "Generierung läuft",
  VALIDATING_OUTPUT: "Ausgabe wird validiert",
  READY: "Fertig",
  FAILED: "Fehlgeschlagen",
};

interface VoiceCloneFormProps {
  /** Notified when a generation is created so the parent can switch tabs. */
  onGenerationCreated?: (id: string) => void;
  /** Optional: the active phase label to display while busy. */
  activePhaseLabel?: string | null;
}

export function VoiceCloneForm({ onGenerationCreated, activePhaseLabel }: VoiceCloneFormProps) {
  const recordingsQuery = useRecordingsQuery();
  const statusQuery = useVoiceCloneStatusQuery();
  const createMutation = useCreateGenerationMutation();
  const toast = useToast();

  const recordings = recordingsQuery.data?.recordings ?? [];
  const busy = statusQuery.data?.busy ?? false;

  const [referenceFilename, setReferenceFilename] = useState<string>("");
  const [referenceText, setReferenceText] = useState<string>("");
  const [targetText, setTargetText] = useState<string>("");
  const [allowQualityWarning, setAllowQualityWarning] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const qualityQuery = useReferenceQualityQuery(referenceFilename || null);

  // Reset the quality-warning checkbox whenever the reference changes.
  useEffect(() => {
    setAllowQualityWarning(false);
    setSubmitError(null);
  }, [referenceFilename]);

  const selectedRecording = useMemo(
    () => recordings.find((r) => r.filename === referenceFilename) ?? null,
    [recordings, referenceFilename],
  );

  const qualityClass: QualityClass | undefined = qualityQuery.data?.quality;
  const qualityWarnings = qualityQuery.data?.voice_clone_reference?.warnings ?? qualityQuery.data?.warnings ?? [];
  const qualityReasons = qualityQuery.data?.voice_clone_reference?.reasons ?? qualityQuery.data?.reasons ?? [];

  const targetTooLong = targetText.length > MAX_TARGET_CHARS;
  const canSubmit =
    !busy &&
    !!referenceFilename &&
    !!referenceText.trim() &&
    !!targetText.trim() &&
    !targetTooLong &&
    (qualityClass !== "REJECT") &&
    (qualityClass !== "REVIEW" || allowQualityWarning);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitError(null);
    createMutation.mutate(
      {
        reference_recording: referenceFilename,
        reference_text: referenceText,
        target_text: targetText,
        language: "German",
        allow_quality_warning: qualityClass === "REVIEW" ? allowQualityWarning : undefined,
      },
      {
        onSuccess: (data) => {
          toast.show({
            title: "Generierung gestartet",
            description: `ID: ${data.id.slice(0, 8)}…`,
            variant: "success",
          });
          onGenerationCreated?.(data.id);
        },
        onError: (err) => {
          const message = err instanceof Error ? err.message : "Unbekannter Fehler";
          setSubmitError(message);
          toast.show({
            title: "Generierung abgelehnt",
            description: message,
            variant: "error",
          });
        },
      },
    );
  };

  if (recordingsQuery.isLoading) {
    return <p className="page__description">Lade Aufnahmen …</p>;
  }
  if (recordingsQuery.isError) {
    return (
      <p className="page__description" role="alert">
        Aufnahmen konnten nicht geladen werden.
      </p>
    );
  }
  if (recordings.length === 0) {
    return (
      <p className="page__description">
        Nimm zuerst eine Sprachreferenz im Tab „Aufnahmen“ auf.
      </p>
    );
  }

  return (
    <form className="voice-clone-form" onSubmit={handleSubmit}>
      <div className="voice-clone-form__row">
        <label htmlFor="voice-clone-reference" className="voice-clone-form__label">
          Referenzaufnahme
        </label>
        <select
          id="voice-clone-reference"
          className="voice-clone-form__select"
          value={referenceFilename}
          onChange={(e) => setReferenceFilename(e.target.value)}
          disabled={busy}
          aria-label="Referenzaufnahme auswählen"
        >
          <option value="">— Aufnahme wählen —</option>
          {recordings.map((r) => (
            <option key={r.filename} value={r.filename}>
              {r.filename} ({r.duration_seconds.toFixed(1)}s)
            </option>
          ))}
        </select>
      </div>

      {selectedRecording && (
        <div className="voice-clone-form__preview">
          <audio
            controls
            preload="none"
            src={`${selectedRecording.audio_url}?t=${Date.now()}`}
            aria-label={`Referenz ${selectedRecording.filename} abspielen`}
          />
          <div className="voice-clone-form__quality">
            {qualityQuery.isLoading && <span className="page__description">Qualitätsanalyse läuft …</span>}
            {qualityQuery.isError && (
              <span className="page__description" role="alert">
                Qualitätsanalyse fehlgeschlagen.
              </span>
            )}
            {qualityClass && (
              <>
                <Badge variant={QUALITY_BADGE[qualityClass].variant} title={qualityReasons.join("; ")}>
                  Qualität: {QUALITY_BADGE[qualityClass].label}
                </Badge>
                {qualityWarnings.length > 0 && (
                  <ul className="voice-clone-form__warnings" role="note">
                    {qualityWarnings.map((w, i) => (
                      <li key={i}>
                        <AlertTriangle size={12} /> {w}
                      </li>
                    ))}
                  </ul>
                )}
                {qualityReasons.length > 0 && (
                  <ul className="voice-clone-form__reasons" role="note">
                    {qualityReasons.map((r, i) => (
                      <li key={i}>
                        <AlertTriangle size={12} /> {r}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      )}

      <div className="voice-clone-form__row">
        <label htmlFor="voice-clone-ref-text" className="voice-clone-form__label">
          Exakter Referenztext
        </label>
        <textarea
          id="voice-clone-ref-text"
          className="voice-clone-form__textarea"
          value={referenceText}
          onChange={(e) => setReferenceText(e.target.value)}
          disabled={busy}
          rows={2}
          placeholder="Der exakt gesprochene Text der Referenzaufnahme."
          aria-label="Exakter Referenztext"
        />
      </div>

      <div className="voice-clone-form__row">
        <label htmlFor="voice-clone-target-text" className="voice-clone-form__label">
          Zieltext
        </label>
        <textarea
          id="voice-clone-target-text"
          className="voice-clone-form__textarea"
          value={targetText}
          onChange={(e) => setTargetText(e.target.value)}
          disabled={busy}
          rows={3}
          maxLength={MAX_TARGET_CHARS + 50}
          placeholder="Der neu zu erzeugende Text (max. 300 Zeichen)."
          aria-label="Zieltext"
        />
        <div className="voice-clone-form__counter" aria-live="polite">
          <span className={targetTooLong ? "voice-clone-form__counter--over" : ""}>
            {targetText.length} / {MAX_TARGET_CHARS}
          </span>
        </div>
      </div>

      {qualityClass === "REVIEW" && (
        <label className="voice-clone-form__checkbox">
          <input
            type="checkbox"
            checked={allowQualityWarning}
            onChange={(e) => setAllowQualityWarning(e.target.checked)}
            disabled={busy}
          />
          <span>
            <AlertTriangle size={14} /> Ich habe die Qualitätswarnungen gesehen und möchte trotzdem fortfahren.
          </span>
        </label>
      )}

      {qualityClass === "REJECT" && (
        <p className="voice-clone-form__reject" role="alert">
          <AlertTriangle size={14} /> Die Referenz wurde technisch abgelehnt (REJECT). Generierung ist nicht möglich.
        </p>
      )}

      {submitError && (
        <p className="voice-clone-form__reject" role="alert">
          <AlertTriangle size={14} /> {submitError}
        </p>
      )}

      <div className="voice-clone-form__actions">
        <Button type="submit" variant="primary" disabled={!canSubmit} loading={createMutation.isPending}>
          {busy ? (
            <>
              <Loader2 size={14} className="spin" /> Generierung läuft …
            </>
            ) : (
            <>
              <Wand2 size={14} /> Generierung starten
            </>
            )}
        </Button>
        {busy && (
          <span className="voice-clone-form__busy-note">
            Eine zweite Generierung ist blockiert, bis die aktuelle fertig ist.
          </span>
        )}
        {canSubmit && !busy && (
          <span className="voice-clone-form__ready-note">
            <CheckCircle2 size={14} /> Bereit zum Start.
          </span>
        )}
      </div>

      {busy && activePhaseLabel && (
        <p className="voice-clone-form__phase" aria-live="polite">
          Aktive Phase: {PHASE_LABELS[activePhaseLabel] ?? activePhaseLabel}
        </p>
      )}
    </form>
  );
}
