import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Wand2, AlertTriangle, CheckCircle2, Loader2, ArrowRight } from "lucide-react";
import {
  useCreateGenerationMutation,
  useReferenceQualityQuery,
  useVoiceCloneStatusQuery,
} from "../../hooks/useVoiceClone";
import { useUploadRecordingMutation } from "../../hooks/useQueries";
import {
  useVoiceProfilesQuery,
  useVoiceProfileQuery,
} from "../../features/voiceProfiles/hooks";
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
  /** Notified when a generation is created so the parent can react. */
  onGenerationCreated?: (id: string) => void;
  /** Optional: the active phase label to display while busy. */
  activePhaseLabel?: string | null;
}

type CloneMode = "profile" | "manual";

export function VoiceCloneForm({ onGenerationCreated, activePhaseLabel }: VoiceCloneFormProps) {
  const statusQuery = useVoiceCloneStatusQuery();
  const createMutation = useCreateGenerationMutation();
  const uploadMutation = useUploadRecordingMutation();
  const profilesQuery = useVoiceProfilesQuery();
  const toast = useToast();

  const statusData = statusQuery.data;
  const available = statusData?.available ?? false;
  const busy = statusData?.busy ?? false;
  const runtimeReasons = statusData?.reasons ?? [];
  const runtimeWarnings = statusData?.warnings ?? [];
  const activeGenerationId = statusData?.active_generation_id ?? null;

  // Default to "Aus Voice-Profil"; manual upload is the optional alternative.
  const [mode, setMode] = useState<CloneMode>("profile");
  const [referenceFilename, setReferenceFilename] = useState<string>("");
  const [referenceText, setReferenceText] = useState<string>("");
  const [targetText, setTargetText] = useState<string>("");
  const [allowQualityWarning, setAllowQualityWarning] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string>("");
  const [selectedProfileScriptId, setSelectedProfileScriptId] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const profileQuery = useVoiceProfileQuery(selectedProfileId || null);
  const qualityQuery = useReferenceQualityQuery(mode === "manual" ? referenceFilename || null : null);

  // Reset quality-warning + errors whenever the reference or mode changes.
  useEffect(() => {
    setAllowQualityWarning(false);
    setSubmitError(null);
  }, [referenceFilename, mode]);

  // Reset script selection when switching profiles.
  useEffect(() => {
    setSelectedProfileScriptId("");
  }, [selectedProfileId]);

  const profiles = profilesQuery.data?.profiles ?? [];
  const selectedProfile = profileQuery.data ?? null;
  // Only ACCEPTED references are eligible for voice clone.
  const acceptedReferences = useMemo(() => {
    if (!selectedProfile?.references) return [];
    return Object.values(selectedProfile.references).filter(
      (r) => r.status === "ACCEPTED",
    );
  }, [selectedProfile?.references]);
  const selectedProfileReference = useMemo(
    () => acceptedReferences.find((r) => r.script_id === selectedProfileScriptId) ?? null,
    [acceptedReferences, selectedProfileScriptId],
  );

  const qualityClass: QualityClass | undefined =
    mode === "manual" ? qualityQuery.data?.quality : undefined;
  const qualityWarnings = qualityQuery.data?.voice_clone_reference?.warnings ?? qualityQuery.data?.warnings ?? [];
  const qualityReasons = qualityQuery.data?.voice_clone_reference?.reasons ?? qualityQuery.data?.reasons ?? [];

  const targetTooLong = targetText.length > MAX_TARGET_CHARS;

  const isUploading = uploadMutation.isPending;

  // Manual mode submit readiness. Requires an uploaded reference (with a
  // server-known filename), the exact reference text, target text, and a
  // non-REJECT quality class.
  const manualCanSubmit =
    available &&
    !busy &&
    !!referenceFilename &&
    !!referenceText.trim() &&
    !!targetText.trim() &&
    !targetTooLong &&
    !isUploading &&
    (qualityClass !== "REJECT") &&
    (qualityClass !== "REVIEW" || allowQualityWarning);

  // Profile mode submit readiness. The server resolves the WAV and text;
  // the client only needs a profile, an accepted reference, and target text.
  const profileCanSubmit =
    available &&
    !busy &&
    !!selectedProfileId &&
    !!selectedProfileReference &&
    !!targetText.trim() &&
    !targetTooLong;

  const canSubmit = mode === "manual" ? manualCanSubmit : profileCanSubmit;

  const handleFileSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadError(null);
    setReferenceFilename("");
    uploadMutation.mutate(
      { blob: file, filename: file.name || "reference.wav" },
      {
        onSuccess: (data) => {
          setReferenceFilename(data.filename);
          toast.show({
            title: "Referenz hochgeladen",
            description: data.filename,
            variant: "success",
          });
        },
        onError: (err) => {
          const message = err instanceof Error ? err.message : "Unbekannter Fehler";
          setUploadError(message);
          toast.show({
            title: "Upload fehlgeschlagen",
            description: message,
            variant: "error",
          });
        },
      },
    );
    // Allow re-selecting the same file after a failed upload.
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitError(null);
    if (mode === "profile") {
      createMutation.mutate(
        {
          voice_profile_id: selectedProfileId,
          voice_profile_script_id: selectedProfileScriptId,
          target_text: targetText,
          language: "German",
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
      return;
    }
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

  return (
    <form className="voice-clone-form" onSubmit={handleSubmit}>
      {!available && (
        <div className="voice-clone-form__unavailable" role="alert">
          <AlertTriangle size={14} />
          <div>
            <strong>Voice Clone ist aktuell nicht verfügbar.</strong>
            {runtimeReasons.length > 0 ? (
              <ul className="voice-clone-form__reasons">
                {runtimeReasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            ) : (
              <p>Generierungen können erst gestartet werden, wenn das Backend Qwen3-TTS bereitstellt.</p>
            )}
            {runtimeWarnings.length > 0 && (
              <ul className="voice-clone-form__warnings" role="note">
                {runtimeWarnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      <div className="voice-clone-form__mode" role="group" aria-label="Referenzmodus">
        <button
          type="button"
          className={`btn btn--sm ${mode === "profile" ? "btn--primary" : "btn--secondary"}`}
          aria-pressed={mode === "profile"}
          onClick={() => setMode("profile")}
        >
          Aus Voice-Profil
        </button>
        <button
          type="button"
          className={`btn btn--sm ${mode === "manual" ? "btn--primary" : "btn--secondary"}`}
          aria-pressed={mode === "manual"}
          onClick={() => setMode("manual")}
        >
          Manueller Upload
        </button>
      </div>

      {mode === "manual" && (
        <>
          <div className="voice-clone-form__row">
            <label htmlFor="voice-clone-reference-file" className="voice-clone-form__label">
              Referenzaufnahme (WAV)
            </label>
            <input
              ref={fileInputRef}
              id="voice-clone-reference-file"
              type="file"
              accept="audio/wav,audio/x-wav,audio/wave,audio/*"
              onChange={handleFileSelected}
              disabled={busy || isUploading}
              aria-label="Referenzaufnahme hochladen"
            />
            {referenceFilename && !isUploading && (
              <p className="page__description" style={{ marginTop: 4 }}>
                Hochgeladen: <code style={{ fontFamily: "var(--font-mono)" }}>{referenceFilename}</code>
              </p>
            )}
            {isUploading && (
              <p className="page__description" style={{ marginTop: 4 }}>
                <Loader2 size={12} className="spin" /> Upload läuft …
              </p>
            )}
            {uploadError && (
              <p className="voice-clone-form__reject" role="alert" style={{ marginTop: 4 }}>
                <AlertTriangle size={14} /> {uploadError}
              </p>
            )}
          </div>

          {referenceFilename && !isUploading && (
            <div className="voice-clone-form__preview">
              <audio
                controls
                preload="none"
                src={`/api/recordings/${encodeURIComponent(referenceFilename)}`}
                aria-label={`Referenz ${referenceFilename} abspielen`}
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
        </>
      )}

      {mode === "profile" && (
        <>
          {profilesQuery.isLoading && (
            <p className="page__description">Lade Voice-Profile …</p>
          )}
          {profilesQuery.isError && (
            <p className="page__description" role="alert">
              Voice-Profile konnten nicht geladen werden.
            </p>
          )}
          {!profilesQuery.isLoading && !profilesQuery.isError && profiles.length === 0 && (
            <div className="voice-clone-form__empty-profiles">
              <p className="page__description">
                Es ist noch kein Voice-Profil vorhanden. Erstelle eines und hinterlege
                akzeptierte Referenzen, um daraus einen Voice-Clone zu erzeugen.
              </p>
              <Link
                to="/voice-lab"
                className="btn btn--primary btn--sm"
                style={{ alignSelf: "flex-start" }}
              >
                Voice-Profil erstellen <ArrowRight size={14} />
              </Link>
            </div>
          )}
          {profiles.length > 0 && (
            <>
              <div className="voice-clone-form__row">
                <label htmlFor="voice-clone-profile" className="voice-clone-form__label">
                  Voice-Profil
                </label>
                <select
                  id="voice-clone-profile"
                  className="voice-clone-form__select"
                  value={selectedProfileId}
                  onChange={(e) => setSelectedProfileId(e.target.value)}
                  disabled={busy}
                  aria-label="Voice-Profil auswählen"
                >
                  <option value="">— Profil wählen —</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.progress.accepted}/{p.progress.total} akzeptiert)
                    </option>
                  ))}
                </select>
              </div>

              {selectedProfileId && profileQuery.isLoading && (
                <p className="page__description">Lade Profilreferenzen …</p>
              )}
              {selectedProfileId && profileQuery.isError && (
                <p className="page__description" role="alert">
                  Profil konnte nicht geladen werden.
                </p>
              )}

              {selectedProfile && (
                <div className="voice-clone-form__row">
                  <label htmlFor="voice-clone-profile-ref" className="voice-clone-form__label">
                    Akzeptierte Referenz ({acceptedReferences.length})
                  </label>
                  {acceptedReferences.length === 0 ? (
                    <p className="page__description">
                      Dieses Profil hat noch keine akzeptierten Referenzen.
                    </p>
                  ) : (
                    <select
                      id="voice-clone-profile-ref"
                      className="voice-clone-form__select"
                      value={selectedProfileScriptId}
                      onChange={(e) => setSelectedProfileScriptId(e.target.value)}
                      disabled={busy}
                      aria-label="Akzeptierte Profilreferenz auswählen"
                    >
                      <option value="">— Referenz wählen —</option>
                      {acceptedReferences.map((r) => (
                        <option key={r.script_id} value={r.script_id}>
                          {r.script_id} · {r.recording_filename}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}

              {selectedProfileReference && (
                <div className="voice-clone-form__preview">
                  <audio
                    controls
                    preload="none"
                    src={`/api/recordings/${encodeURIComponent(selectedProfileReference.recording_filename)}`}
                    aria-label={`Referenz ${selectedProfileReference.recording_filename} abspielen`}
                  />
                  <div className="voice-clone-form__quality">
                    <Badge variant="success">
                      Qualität: {selectedProfileReference.quality_class}
                    </Badge>
                    <p className="page__description" style={{ marginTop: 4 }}>
                      Referenztext (vom Server, nicht editierbar):
                    </p>
                    <p className="voice-clone-form__profile-ref-text">
                      {selectedProfileReference.script_text}
                    </p>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

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

      {mode === "manual" && qualityClass === "REVIEW" && (
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

      {mode === "manual" && qualityClass === "REJECT" && (
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
            {activeGenerationId && (
              <> Aktive Generierung: <code style={{ fontFamily: "var(--font-mono)" }}>{activeGenerationId.slice(0, 12)}</code></>
            )}
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

export { PHASE_LABELS };
