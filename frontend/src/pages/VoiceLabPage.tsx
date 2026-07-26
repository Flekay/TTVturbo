import { useState } from "react";
import { Mic } from "lucide-react";
import { AudioRecorder } from "../components/recordings/AudioRecorder";
import { RecordingList } from "../components/recordings/RecordingList";
import { useToast } from "../components/ui/ToastProvider";

export function VoiceLabPage() {
  const toast = useToast();
  const [recorderKey, setRecorderKey] = useState(0);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Voice Lab</h1>
          <p className="page__description">
            Nimm Sprachreferenzen auf und verwalte vorhandene Aufnahmen.
          </p>
        </div>
      </div>

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
    </div>
  );
}
