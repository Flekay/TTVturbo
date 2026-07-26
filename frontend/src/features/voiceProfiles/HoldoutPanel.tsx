import { LoadingState } from "../../components/ui/LoadingState";
import { ErrorState } from "../../components/ui/ErrorState";
import type { VoiceScript } from "./types";

interface HoldoutPanelProps {
  scripts: VoiceScript[];
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}

export function HoldoutPanel({
  scripts,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: HoldoutPanelProps) {
  if (isLoading) {
    return <LoadingState message="Lade Holdout-Skripte …" />;
  }
  if (isError) {
    return (
      <ErrorState
        title="Holdout-Skripte konnten nicht geladen werden"
        message={errorMessage ?? "Unbekannter Fehler"}
        onRetry={onRetry}
      />
    );
  }
  if (scripts.length === 0) {
    return null;
  }

  return (
    <section className="vp-holdout" aria-label="Holdout-Skripte">
      <h3 className="vp-section-title">Holdout-Skripte</h3>
      <p className="vp-holdout__notice" role="note">
        Nur zur späteren Qualitätsprüfung. Nicht als Referenz aufnehmen.
      </p>
      <ul className="vp-holdout__list">
        {scripts.map((script) => (
          <li key={script.id} className="vp-holdout__item">
            <div className="vp-holdout__item-meta">
              #{script.order}
              {script.style && ` · ${script.style}`}
              {script.category && ` · ${script.category}`}
            </div>
            {script.text}
          </li>
        ))}
      </ul>
    </section>
  );
}
