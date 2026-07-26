import { useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Mic, Wand2, ListChecks, Users, X } from "lucide-react";
import { AudioRecorder } from "../components/recordings/AudioRecorder";
import { RecordingList } from "../components/recordings/RecordingList";
import { VoiceCloneForm } from "../components/voiceClone/VoiceCloneForm";
import { GenerationList } from "../components/voiceClone/GenerationList";
import { useToast } from "../components/ui/ToastProvider";
import {
  useGenerationsQuery,
  useVoiceCloneStatusQuery,
} from "../hooks/useVoiceClone";
import { useAttachReferenceMutation } from "../features/voiceProfiles/hooks";
import type { GenerationStatus } from "../types/voiceClone";
import type { KnownGenerationStatus } from "../types/schemas";
import { VoiceProfilesPanel } from "../features/voiceProfiles";
import type { PromptRecordingRequest } from "../features/voiceProfiles/types";

type VoiceLabTab = "recordings" | "voice-profiles" | "voice-clone" | "generations";

const ACTIVE_STATUSES = new Set<KnownGenerationStatus>([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
]);

const PHASE_LABELS: Record<KnownGenerationStatus, string> = {
  QUEUED: "Warteschlange",
  VALIDATING_REFERENCE: "Referenz wird geprüft",
  LOADING_MODEL: "Modell wird geladen",
  GENERATING: "Generierung läuft",
  VALIDATING_OUTPUT: "Ausgabe wird validiert",
  READY: "Fertig",
  FAILED: "Fehlgeschlagen",
};

/**
 * A pending guided recording started from the Voice Profiles tab.
 *
 * When set, the recorder is opened in guided mode: the exact script text is
 * shown prominently and the uploaded WAV is automatically attached to the
 * profile + script after a successful upload.
 */
type PendingPromptRecording = PromptRecordingRequest | null;

export function VoiceLabPage() {
  const toast = useToast();
  const [recorderKey, setRecorderKey] = useState(0);
  const [tab, setTab] = useState<VoiceLabTab>("recordings");
  const [pendingRecording, setPendingRecording] = useState<PendingPromptRecording>(null);
  const attachMutation = useAttachReferenceMutation();

  const statusQuery = useVoiceCloneStatusQuery();
  const generationsQuery = useGenerationsQuery();

  // Derive the active generation's phase label for display in the form.
  const activePhaseLabel = useMemo<GenerationStatus | null>(() => {
    const activeId = statusQuery.data?.active_generation_id;
    if (!activeId) return null;
    const gen = generationsQuery.data?.generations.find((g) => g.id === activeId);
    return gen?.status ?? null;
  }, [statusQuery.data, generationsQuery.data]);

  const handleGenerationCreated = () => {
    setTab("generations");
  };

  const handleStartPromptRecording = (request: PromptRecordingRequest) => {
    setPendingRecording(request);
    setTab("recordings");
  };

  const handleGuidedUploadSuccess = (filename: string) => {
    if (!pendingRecording) {
      toast.show({ title: "Aufnahme gespeichert", description: filename, variant: "success" });
      return;
    }
    // Attempt to attach the freshly uploaded WAV to the profile + script.
    attachMutation.mutate(
      {
        profileId: pendingRecording.profileId,
        scriptId: pendingRecording.scriptId,
        request: { recording_filename: filename },
      },
      {
        onSuccess: () => {
          toast.show({
            title: "Aufnahme verknüpft",
            description: `${filename} wurde dem Profil zugeordnet.`,
            variant: "success",
          });
          setPendingRecording(null);
          setTab("voice-profiles");
        },
        onError: (err) => {
          // The WAV was saved, but the attach failed. Do NOT delete the WAV.
          // Surface a clear message with the filename and a retry hint.
          const message = err instanceof Error ? err.message : "Unbekannter Fehler";
          toast.show({
            title: "Aufnahme gespeichert, aber nicht verknüpft",
            description: `${filename}: ${message}. Die Datei bleibt erhalten und kann manuell zugewiesen werden.`,
            variant: "error",
          });
          // Keep the guided mode open so the user can retry or cancel.
        },
      },
    );
  };

  const handleGuidedUploadError = (message: string) => {
    toast.show({ title: "Upload fehlgeschlagen", description: message, variant: "error" });
  };

  const handleCancelGuidedRecording = () => {
    setPendingRecording(null);
  };

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Voice Lab</h1>
          <p className="page__description">
            Sprachreferenzen aufnehmen, Voice-Profile verwalten und echte Qwen3-TTS-Voice-Clones erzeugen.
          </p>
        </div>
      </div>

      <Tabs.Root
        className="voice-lab-tabs"
        value={tab}
        onValueChange={(value) => setTab(value as VoiceLabTab)}
      >
        <Tabs.List className="voice-lab-tabs__list" aria-label="Voice Lab Bereiche">
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="recordings">
            <Mic size={14} /> Aufnahmen
          </Tabs.Trigger>
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="voice-profiles">
            <Users size={14} /> Voice Profiles
          </Tabs.Trigger>
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="voice-clone">
            <Wand2 size={14} /> Voice Clone
          </Tabs.Trigger>
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="generations">
            <ListChecks size={14} /> Generierungen
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content className="voice-lab-tabs__content" value="recordings">
          {pendingRecording && (
            <div className="guided-recording-banner" role="status" aria-live="polite">
              <div className="guided-recording-banner__info">
                <strong>Geführte Aufnahme</strong>
                <span>
                  Profil: {pendingRecording.profileId.slice(0, 8)}… · Skript:{" "}
                  {pendingRecording.scriptId}
                </span>
                <span className="guided-recording-banner__text">
                  {pendingRecording.scriptText}
                </span>
                <span className="guided-recording-banner__hint">
                  Nach erfolgreichem Upload wird diese Aufnahme automatisch mit dem Profil verknüpft.
                </span>
              </div>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={handleCancelGuidedRecording}
              >
                <X size={14} /> Abbrechen
              </button>
            </div>
          )}
          <section className="page__section">
            <h2 className="page__section-title">Recorder</h2>
            <AudioRecorder
              key={recorderKey}
              onUploaded={
                pendingRecording ? handleGuidedUploadSuccess : undefined
              }
              onUploadError={
                pendingRecording ? handleGuidedUploadError : undefined
              }
            />
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setRecorderKey((k) => k + 1)}
              style={{ alignSelf: "flex-start" }}
            >
              <Mic size={14} /> Recorder zurücksetzen
            </button>
          </section>

          <section className="page__section">
            <h2 className="page__section-title">Aufnahmenbibliothek</h2>
            <RecordingList />
          </section>
        </Tabs.Content>

        <Tabs.Content className="voice-lab-tabs__content" value="voice-profiles">
          <section className="page__section">
            <h2 className="page__section-title">Voice Profiles</h2>
            <p className="page__description">
              Erstelle Voice-Profile, nimm die 88 Aufnahmeskripte geführt auf und
              verwalte akzeptierte Referenzen für Voice-Clones.
            </p>
            <VoiceProfilesPanel onStartPromptRecording={handleStartPromptRecording} />
          </section>
        </Tabs.Content>

        <Tabs.Content className="voice-lab-tabs__content" value="voice-clone">
          <section className="page__section">
            <h2 className="page__section-title">Voice Clone (Qwen3-TTS)</h2>
            <p className="page__description">
              Wähle eine echte Aufnahme oder eine akzeptierte Profilreferenz und gib
              einen neuen Zieltext ein. Die Generierung läuft in einem separaten
              Prozess auf der RTX 5070.
            </p>
            <VoiceCloneForm
              onGenerationCreated={handleGenerationCreated}
              activePhaseLabel={activePhaseLabel}
            />
          </section>
        </Tabs.Content>

        <Tabs.Content className="voice-lab-tabs__content" value="generations">
          <section className="page__section">
            <h2 className="page__section-title">Generierungen</h2>
            <GenerationList />
          </section>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

export { PHASE_LABELS, ACTIVE_STATUSES };
