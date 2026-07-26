import { useState } from "react";
import { Download, Trash2, AlertCircle, CheckCircle2, Loader2, ChevronDown, ChevronRight } from "lucide-react";
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
import { GenerationDeleteDialog } from "./GenerationDeleteDialog";
import { KNOWN_GENERATION_STATUSES, type KnownGenerationStatus } from "../../types/schemas";
import type { GenerationMetadata } from "../../types/voiceClone";

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

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function statusBadge(status: string): { variant: "success" | "warning" | "error" | "muted" | "info"; label: string } {
  if ((KNOWN_GENERATION_STATUSES as readonly string[]).includes(status)) {
    return STATUS_BADGE[status as KnownGenerationStatus];
  }
  // Unknown future status: neutral badge, real status string visible.
  return { variant: "muted", label: status };
}

function isActiveStatus(status: string): boolean {
  return ACTIVE_STATUSES.has(status as KnownGenerationStatus);
}

export function GenerationList() {
  const query = useGenerationsQuery();
  const deleteMutation = useDeleteGenerationMutation();
  const toast = useToast();
  const [pendingDelete, setPendingDelete] = useState<GenerationMetadata | null>(null);

  const handleDeleteRequest = (gen: GenerationMetadata) => {
    setPendingDelete(gen);
  };

  const handleConfirmDelete = () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    deleteMutation.mutate(target.id, {
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
        // Keep the dialog open so the user can retry or cancel.
      },
    });
  };

  const handleCancelDelete = () => {
    setPendingDelete(null);
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
    <>
      <ul className="generation-list">
        {generations.map((gen) => (
          <GenerationCard
            key={gen.id}
            gen={gen}
            pendingDeleteId={pendingDelete?.id}
            deleteBusy={deleteMutation.isPending}
            onDeleteRequest={handleDeleteRequest}
          />
        ))}
      </ul>
      <GenerationDeleteDialog
        open={pendingDelete !== null}
        generation={pendingDelete}
        busy={deleteMutation.isPending}
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
      />
    </>
  );
}

interface GenerationCardProps {
  gen: GenerationMetadata;
  pendingDeleteId?: string;
  deleteBusy: boolean;
  onDeleteRequest: (gen: GenerationMetadata) => void;
}

function GenerationCard({ gen, pendingDeleteId, deleteBusy, onDeleteRequest }: GenerationCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const isActive = isActiveStatus(gen.status);
  const isReady = gen.status === "READY";
  const isFailed = gen.status === "FAILED";
  const badge = statusBadge(gen.status);
  const isPendingDelete = pendingDeleteId === gen.id;

  const technicalDetails: Array<{ label: string; value: string }> = [];
  if (gen.model_id) technicalDetails.push({ label: "Modell", value: gen.model_id });
  if (gen.model_revision && gen.model_revision !== "unknown") {
    technicalDetails.push({ label: "Revision", value: gen.model_revision });
  }
  if (gen.device_name) technicalDetails.push({ label: "GPU", value: gen.device_name });
  if (gen.peak_vram_bytes != null) {
    technicalDetails.push({ label: "Peak-VRAM", value: formatBytes(gen.peak_vram_bytes) });
  }
  if (gen.output_sample_rate != null) {
    technicalDetails.push({ label: "Sample-Rate", value: `${gen.output_sample_rate} Hz` });
  }
  if (gen.output_sha256) {
    technicalDetails.push({ label: "SHA-256", value: gen.output_sha256.slice(0, 12) + "…" });
  }
  if (gen.reference_sha256) {
    technicalDetails.push({ label: "Ref-SHA-256", value: gen.reference_sha256.slice(0, 12) + "…" });
  }
  if (gen.worker_exit_code != null) {
    technicalDetails.push({ label: "Worker-Exitcode", value: String(gen.worker_exit_code) });
  }
  if (gen.warnings.length > 0) {
    technicalDetails.push({ label: "Warnungen", value: gen.warnings.join("; ") });
  }

  return (
    <li className="generation-card">
      <div className="generation-card__header">
        <Badge variant={badge.variant} title={gen.status}>{badge.label}</Badge>
        <span className="generation-card__date">{formatCreatedAt(gen.created_at)}</span>
        {isActive && <Loader2 size={14} className="spin" aria-hidden="true" />}
        {isReady && <CheckCircle2 size={14} aria-hidden="true" />}
        {isFailed && <AlertCircle size={14} aria-hidden="true" />}
      </div>
      <div className="generation-card__meta">
        <div className="generation-card__ref">
          Referenz: <span title={gen.reference_recording}>{gen.reference_recording}</span>
        </div>
        {gen.voice_profile_name && (
          <div className="generation-card__ref">
            Voice-Profil: <span>{gen.voice_profile_name}</span>
            {gen.voice_profile_script_id && (
              <span style={{ color: "var(--color-text-muted)" }}>
                {" "}· {gen.voice_profile_script_id}
              </span>
            )}
          </div>
        )}
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
            src={generationAudioUrl(gen.id)}
            aria-label={`Audio-Player für Generierung ${gen.id}`}
          />
        </div>
      )}
      {technicalDetails.length > 0 && (
        <div className="generation-card__details">
          <button
            type="button"
            className="btn btn--ghost btn--sm generation-card__details-toggle"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((v) => !v)}
          >
            {detailsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Technische Details
          </button>
          {detailsOpen && (
            <dl className="generation-card__details-list">
              {technicalDetails.map((d) => (
                <div key={d.label} className="generation-card__details-row">
                  <dt>{d.label}</dt>
                  <dd>{d.value}</dd>
                </div>
              ))}
            </dl>
          )}
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
          onClick={() => onDeleteRequest(gen)}
          loading={isPendingDelete && deleteBusy}
          disabled={isActive}
          aria-label={`Generierung ${gen.id.slice(0, 8)} löschen`}
          title={isActive ? "Löschen während der Generierung ist blockiert" : undefined}
        >
          <Trash2 size={14} /> Löschen
        </Button>
      </div>
    </li>
  );
}
