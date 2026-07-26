import { useMemo } from "react";
import { Download, AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { VoiceCloneForm } from "../components/voiceClone/VoiceCloneForm";
import { Badge } from "../components/ui/Badge";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";
import {
  useGenerationsQuery,
  useVoiceCloneStatusQuery,
} from "../hooks/useVoiceClone";
import { generationAudioUrl } from "../api/voiceClone";
import { KNOWN_GENERATION_STATUSES, type KnownGenerationStatus } from "../types/schemas";
import type { GenerationMetadata } from "../types/voiceClone";

const STATUS_BADGE: Record<KnownGenerationStatus, { variant: "success" | "warning" | "error" | "muted" | "info"; label: string }> = {
  QUEUED: { variant: "muted", label: "Queued" },
  VALIDATING_REFERENCE: { variant: "info", label: "Referenzprüfung" },
  LOADING_MODEL: { variant: "info", label: "Modell laden" },
  GENERATING: { variant: "info", label: "Generierung" },
  VALIDATING_OUTPUT: { variant: "info", label: "Validierung" },
  READY: { variant: "success", label: "Fertig" },
  FAILED: { variant: "error", label: "Fehlgeschlagen" },
};

const ACTIVE_STATUSES: ReadonlySet<KnownGenerationStatus> = new Set<KnownGenerationStatus>([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
]);

function statusBadge(status: string): { variant: "success" | "warning" | "error" | "muted" | "info"; label: string } {
  if ((KNOWN_GENERATION_STATUSES as readonly string[]).includes(status)) {
    return STATUS_BADGE[status as KnownGenerationStatus];
  }
  return { variant: "muted", label: status };
}

function formatCreatedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

export function VoiceClonePage() {
  const statusQuery = useVoiceCloneStatusQuery();
  const generationsQuery = useGenerationsQuery();

  const activePhaseLabel = useMemo<string | null>(() => {
    const activeId = statusQuery.data?.active_generation_id;
    if (!activeId) return null;
    const gen = generationsQuery.data?.generations.find((g) => g.id === activeId);
    return gen?.status ?? null;
  }, [statusQuery.data, generationsQuery.data]);

  // Show the active generation (if any) otherwise the most recent one. This
  // keeps the page self-contained: the user triggers a clone and sees the
  // result inline without a separate "Generierungen" tab.
  const latestGeneration = useMemo<GenerationMetadata | null>(() => {
    const gens = generationsQuery.data?.generations ?? [];
    if (gens.length === 0) return null;
    const activeId = statusQuery.data?.active_generation_id;
    if (activeId) {
      const active = gens.find((g) => g.id === activeId);
      if (active) return active;
    }
    // Generations are returned newest-first by the backend.
    return gens[0];
  }, [generationsQuery.data, statusQuery.data]);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Voice Clone</h1>
          <p className="page__description">
            On-Demand Voice-Clone mit Qwen3-TTS. Standardmäßig aus einem Voice-Profil,
            optional per manuellem WAV-Upload.
          </p>
        </div>
      </div>

      <section className="page__section">
        <h2 className="page__section-title">Voice Clone (Qwen3-TTS)</h2>
        <p className="page__description">
          Wähle eine akzeptierte Profilreferenz oder lade eine WAV-Datei hoch und gib
          den neuen Zieltext ein. Die Generierung läuft auf der GPU.
        </p>
        <VoiceCloneForm activePhaseLabel={activePhaseLabel} />
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Aktuelle Generierung</h2>
        {generationsQuery.isLoading ? (
          <LoadingState message="Lade Generierungen …" />
        ) : generationsQuery.isError ? (
          <ErrorState
            title="Generierungen konnten nicht geladen werden"
            message={
              generationsQuery.error instanceof Error
                ? generationsQuery.error.message
                : "Unbekannter Fehler"
            }
            onRetry={() => void generationsQuery.refetch()}
          />
        ) : !latestGeneration ? (
          <p className="page__description">
            Noch keine Generierung. Starte oben eine Voice-Clone-Generierung.
          </p>
        ) : (
          <LatestGenerationCard gen={latestGeneration} />
        )}
      </section>
    </div>
  );
}

function LatestGenerationCard({ gen }: { gen: GenerationMetadata }) {
  const isActive = ACTIVE_STATUSES.has(gen.status as KnownGenerationStatus);
  const isReady = gen.status === "READY";
  const isFailed = gen.status === "FAILED";
  const badge = statusBadge(gen.status);

  return (
    <div className="generation-card" aria-label="Aktuelle Generierung">
      <div className="generation-card__header">
        <Badge variant={badge.variant} title={gen.status}>{badge.label}</Badge>
        <span className="generation-card__date">{formatCreatedAt(gen.created_at)}</span>
        <code style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
          {gen.id.slice(0, 12)}
        </code>
      </div>

      <div className="generation-card__body">
        <div className="generation-card__texts">
          <div>
            <strong>Zieltext:</strong> {truncate(gen.target_text, 160)}
          </div>
          {gen.voice_profile_name && (
            <div>
              <strong>Profil:</strong> {gen.voice_profile_name}
            </div>
          )}
          {gen.failure_reason && (
            <div className="generation-card__failure" role="alert">
              <AlertCircle size={14} /> {gen.failure_reason}
            </div>
          )}
        </div>

        {isActive && (
          <p className="page__description">
            <Loader2 size={14} className="spin" /> Generierung läuft …
          </p>
        )}

        {isReady && (
          <div className="generation-card__output">
            <audio
              controls
              preload="none"
              src={generationAudioUrl(gen.id)}
              aria-label={`Audio-Player für Generierung ${gen.id}`}
            />
            <a
              className="btn btn--ghost btn--sm"
              href={generationAudioUrl(gen.id)}
              download
              aria-label="Generierung herunterladen"
            >
              <Download size={14} /> Herunterladen
            </a>
          </div>
        )}

        {isFailed && (
          <p className="page__description">
            <AlertCircle size={14} /> Die Generierung ist fehlgeschlagen.
          </p>
        )}

        {!isActive && !isReady && !isFailed && (
          <p className="page__description">
            <CheckCircle2 size={14} /> Status: {gen.status}
          </p>
        )}
      </div>
    </div>
  );
}
