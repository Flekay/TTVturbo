import { useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Mic, Wand2, ListChecks } from "lucide-react";
import { AudioRecorder } from "../components/recordings/AudioRecorder";
import { RecordingList } from "../components/recordings/RecordingList";
import { VoiceCloneForm } from "../components/voiceClone/VoiceCloneForm";
import { GenerationList } from "../components/voiceClone/GenerationList";
import { useToast } from "../components/ui/ToastProvider";
import {
  useGenerationsQuery,
  useVoiceCloneStatusQuery,
} from "../hooks/useVoiceClone";
import type { GenerationStatus } from "../types/voiceClone";

type VoiceLabTab = "recordings" | "voice-clone" | "generations";

const ACTIVE_STATUSES = new Set<GenerationStatus>([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
]);

const PHASE_LABELS: Record<GenerationStatus, string> = {
  QUEUED: "Warteschlange",
  VALIDATING_REFERENCE: "Referenz wird geprüft",
  LOADING_MODEL: "Modell wird geladen",
  GENERATING: "Generierung läuft",
  VALIDATING_OUTPUT: "Ausgabe wird validiert",
  READY: "Fertig",
  FAILED: "Fehlgeschlagen",
};

export function VoiceLabPage() {
  const toast = useToast();
  const [recorderKey, setRecorderKey] = useState(0);
  const [tab, setTab] = useState<VoiceLabTab>("recordings");

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

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Voice Lab</h1>
          <p className="page__description">
            Sprachreferenzen aufnehmen, echte Qwen3-TTS-Voice-Clones erzeugen und verwalten.
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
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="voice-clone">
            <Wand2 size={14} /> Voice Clone
          </Tabs.Trigger>
          <Tabs.Trigger className="voice-lab-tabs__trigger" value="generations">
            <ListChecks size={14} /> Generierungen
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content className="voice-lab-tabs__content" value="recordings">
          <section className="page__section">
            <h2 className="page__section-title">Recorder</h2>
            <AudioRecorder
              key={recorderKey}
              onUploaded={(filename) =>
                toast.show({
                  title: "Aufnahme gespeichert",
                  description: filename,
                  variant: "success",
                })
              }
              onUploadError={(message) =>
                toast.show({
                  title: "Upload fehlgeschlagen",
                  description: message,
                  variant: "error",
                })
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

        <Tabs.Content className="voice-lab-tabs__content" value="voice-clone">
          <section className="page__section">
            <h2 className="page__section-title">Voice Clone (Qwen3-TTS)</h2>
            <p className="page__description">
              Wähle eine echte Aufnahme, gib den exakt gesprochenen Referenztext und
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
