import { useState } from "react";
import { Download, Trash2, AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import {
  useDeleteGenerationMutation,
  useGenerationsQuery,
} from "../../hooks/useVoiceClone";
import { generationAudioUrl } from "../../api/voiceClone";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { useToast } from "../ui/ToastProvider";
import type { GenerationMetadata, GenerationStatus } from "../../types/voiceClone";

const STATUS_BADGE: Record<GenerationStatus, { variant: "success" | "warning" | "error" | "muted" | "info"; label: string }> = {
  QUEUED: { variant: "muted", label: "Queued" },
  VALIDATING_REFERENCE: { variant: "info", label: "Referenzprüfung" },
  LOADING_MODEL: { variant: "info", label: "Modell laden" },
  GENERATING: { variant: "info", label: "Generierung" },
  VALIDATING_OUTPUT: { variant: "info", label: "Validierung" },
  READY: { variant: "success", label: "Fertig" },
  FAILED: { variant: "error", label: "Fehlgeschlagen" },
};

const ACTIVE_STATUSES = new Set<GenerationStatus>([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
]);

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
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

export function GenerationList() {
  const query = useGenerationsQuery();
  const deleteMutation = useDeleteGenerationMutation();
  const toast = useToast();
  const [pendingDelete, setPendingDelete] = useState<GenerationMetadata | null>(null);

  const handleDelete = (gen: GenerationMetadata) => {
    setPendingDelete(gen);
    deleteMutation.mutate(gen.id, {
      onSuccess: () => {
        toast.show({ title: "Generierung gelöscht", variant: "success" });
        setPendingDelete(null);
      },
      onError: (err) => {
        toast.show({
          title: "Löschen fehlgeschlagen",
          description: err instanceof Error ? err.message : "Unbekannter Fehler",
          variant: "error",
        });
        setPendingDelete(null);
      },
    });
  };

  if (query.isLoading) {
    return <LoadingState message="Lade Generierungen …" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        title="Generierungen konnten nicht geladen werden"
        message={query.error instanceof Error ? query.error.message : "Unbekannter Fehler"}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const generations = query.data?.generations ?? [];
  if (generations.length === 0) {
    return (
      <EmptyState
        title="Noch keine Generierungen"
        description="Starte im Tab „Voice Clone“ eine Qwen3-TTS-Generierung."
      />
    );
  }

  return (
    <ul className="generation-list">
      {generations.map((gen) => {
        const isActive = ACTIVE_STATUSES.has(gen.status);
        const isReady = gen.status === "READY";
        const isFailed = gen.status === "FAILED";
        const badge = STATUS_BADGE[gen.status];
        return (
          <li key={gen.id} className="generation-card">
            <div className="generation-card__header">
              <Badge variant={badge.variant}>{badge.label}</Badge>
              <span className="generation-card__date">{formatCreatedAt(gen.created_at)}</span>
              {isActive && <Loader2 size={14} className="spin" aria-hidden="true" />}
              {isReady && <CheckCircle2 size={14} aria-hidden="true" />}
              {isFailed && <AlertCircle size={14} aria-hidden="true" />}
            </div>
            <div className="generation-card__meta">
              <div className="generation-card__ref">
                Referenz: <span title={gen.reference_recording}>{gen.reference_recording}</span>
              </div>
              <div className="generation-card__target" title={gen.target_text}>
                Ziel: {truncate(gen.target_text, 80)}
              </div>
              <div className="generation-card__stats">
                {gen.generation_seconds != null && (
                  <span>Dauer: {gen.generation_seconds.toFixed(1)}s</span>
                )}
                {gen.output_duration_seconds != null && (
                  <span>Audio: {formatDuration(gen.output_duration_seconds)}</span>
                )}
                {gen.model_revision && gen.model_revision !== "unknown" && (
                  <span>Rev: {gen.model_revision.slice(0, 8)}</span>
                )}
              </div>
            </div>
            {isFailed && gen.failure_reason && (
              <p className="generation-card__error" role="alert">
                <AlertCircle size={14} /> {gen.failure_reason}
              </p>
            )}
            {isReady && (
              <div className="generation-card__player-row">
                <audio
                  className="generation-card__audio"
                  controls
                  preload="none"
                  src={`${generationAudioUrl(gen.id)}?t=${Date.now()}`}
                  aria-label={`Audio-Player für Generierung ${gen.id}`}
                />
              </div>
            )}
            <div className="generation-card__actions">
              {isReady && (
                <a
                  className="btn btn--secondary btn--sm"
                  href={generationAudioUrl(gen.id)}
                  download="output.wav"
                  aria-label={`Generierung ${gen.id.slice(0, 8)} herunterladen`}
                >
                  <Download size={14} /> Download
                </a>
              )}
              <Button
                variant="danger"
                size="sm"
                onClick={() => handleDelete(gen)}
                loading={pendingDelete?.id === gen.id && deleteMutation.isPending}
                disabled={isActive}
                aria-label={`Generierung ${gen.id.slice(0, 8)} löschen`}
                title={isActive ? "Löschen während der Generierung ist blockiert" : undefined}
              >
                <Trash2 size={14} /> Löschen
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
