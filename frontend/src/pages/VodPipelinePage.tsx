import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Workflow, Play, X, RefreshCw, Trash2, ArrowRight, AlertCircle } from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatDateTime, formatDuration } from "../utils/format";
import {
  usePipelineRunsQuery,
  useStartPipelineRunMutation,
  useCancelPipelineRunMutation,
  useRetryPipelineRunMutation,
  useDeletePipelineRunMutation,
} from "../features/mediaProcessing";
import { useVodsQuery } from "../features/vodPipeline";
import type { PipelineRun } from "../features/mediaProcessing";
import type { z } from "zod";
import type { pipelineStepSchema } from "../features/mediaProcessing/schemas";

type PipelineStep = z.infer<typeof pipelineStepSchema>;

const PIPELINE_STEP_LABELS: Record<string, string> = {
  DOWNLOAD: "Download",
  EXTRACT_AUDIO: "Audio-Extraktion",
  TRANSCRIBE: "Transkription",
  FIND_CLIPS: "Clip-Suche",
};

const PIPELINE_STEP_ICONS: Record<string, string> = {
  DOWNLOAD: "⬇",
  EXTRACT_AUDIO: "🎵",
  TRANSCRIBE: "📝",
  FIND_CLIPS: "✂",
};

function pipelineStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY_FOR_CLIP_ANALYSIS":
      return { variant: "success", label: "Bereit für Clip-Analyse" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "WAITING_FOR_GPU":
      return { variant: "info", label: "Wartet auf GPU" };
    case "QUEUED":
      return { variant: "info", label: "Warteschlange" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    default:
      return { variant: "muted", label: status };
  }
}

function stepStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY":
      return { variant: "success", label: "Fertig" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "WAITING":
      return { variant: "muted", label: "Wartet" };
    case "WAITING_FOR_GPU":
      return { variant: "info", label: "Wartet auf GPU" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    case "NOT_IMPLEMENTED":
      return { variant: "muted", label: "Nicht implementiert" };
    default:
      return { variant: "muted", label: status };
  }
}

function isRunActive(status: string): boolean {
  return status === "RUNNING" || status === "WAITING_FOR_GPU" || status === "QUEUED";
}

function PipelineRunCard({
  run,
  vodTitle,
  onCancel,
  onRetry,
  onDelete,
  cancelPending,
  retryPending,
  deletePending,
}: {
  run: PipelineRun;
  vodTitle?: string;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
  cancelPending: boolean;
  retryPending: boolean;
  deletePending: boolean;
}) {
  const status = pipelineStatusBadge(run.status);
  const active = isRunActive(run.status);
  return (
    <Card className="pipeline-run-card">
      <div className="pipeline-run-card__header">
        <div className="pipeline-run-card__title">
          <Badge variant={status.variant}>{status.label}</Badge>
          <span className="pipeline-run-card__vod-title">
            {vodTitle ?? run.source_id}
          </span>
        </div>
        <div className="pipeline-run-card__actions">
          {active && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onCancel}
              disabled={cancelPending}
            >
              <X size={14} />
              Abbrechen
            </Button>
          )}
          {(run.status === "FAILED" || run.status === "CANCELED") && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onRetry}
              disabled={retryPending}
            >
              <RefreshCw size={14} />
              Erneut
            </Button>
          )}
          {!active && (
            <Button
              variant="danger"
              size="sm"
              onClick={onDelete}
              disabled={deletePending}
            >
              <Trash2 size={14} />
              Löschen
            </Button>
          )}
        </div>
      </div>
      <div className="pipeline-run-card__steps">
        {(run.steps ?? []).map((step: PipelineStep, i: number) => {
          const sb = stepStatusBadge(step.status);
          return (
            <div key={i} className="pipeline-step">
              <span className="pipeline-step__icon">
                {PIPELINE_STEP_ICONS[step.type] ?? "•"}
              </span>
              <span className="pipeline-step__label">
                {PIPELINE_STEP_LABELS[step.type] ?? step.type}
              </span>
              <Badge variant={sb.variant}>{sb.label}</Badge>
              {step.error && (
                <span className="pipeline-step__error" title={step.error}>
                  <AlertCircle size={12} />
                </span>
              )}
            </div>
          );
        })}
      </div>
      {run.error && (
        <div className="pipeline-run-card__error">
          <AlertCircle size={14} />
          {run.error}
        </div>
      )}
      <div className="pipeline-run-card__footer">
        <span className="pipeline-run-card__meta">
          Erstellt: {formatDateTime(run.created_at)}
        </span>
        <Link to={`/vod-pipeline/${encodeURIComponent(run.source_id)}`} className="pipeline-run-card__detail-link">
          VOD Details <ArrowRight size={12} />
        </Link>
      </div>
    </Card>
  );
}

/**
 * New VOD Pipeline page.
 *
 * Shows all pipeline runs and lets the user start a new run for any
 * READY VOD. The pipeline orchestrates download -> audio extraction ->
 * transcription using the shared services. The FIND_CLIPS step is
 * shown as NOT_IMPLEMENTED in this phase.
 */
export function VodPipelinePage() {
  // Poll runs while any are active.
  const runsQuery = usePipelineRunsQuery(undefined, { refetchInterval: 3_000 });
  const readyVodsQuery = useVodsQuery({ status: "READY" });

  const startMutation = useStartPipelineRunMutation();
  const cancelMutation = useCancelPipelineRunMutation();
  const retryMutation = useRetryPipelineRunMutation();
  const deleteMutation = useDeletePipelineRunMutation();

  const runs = runsQuery.data?.pipeline_runs ?? [];
  const readyVods = readyVodsQuery.data?.vods ?? [];

  const hasActiveRuns = useMemo(() => runs.some((r) => isRunActive(r.status)), [runs]);

  // Build a map of vod_id -> title for display.
  const vodTitleMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const v of readyVods) {
      m.set(v.id, v.title);
    }
    // Also include VODs from runs that may not be READY anymore.
    for (const r of runs) {
      if (!m.has(r.source_id)) {
        m.set(r.source_id, r.source_id);
      }
    }
    return m;
  }, [readyVods, runs]);

  // VODs that don't already have an active run.
  const startableVods = useMemo(() => {
    const activeSourceIds = new Set(
      runs.filter((r) => isRunActive(r.status)).map((r) => r.source_id),
    );
    return readyVods.filter((v) => !activeSourceIds.has(v.id));
  }, [readyVods, runs]);

  return (
    <div className="page">
      <section className="page__section">
        <h2 className="page__section-title">Pipeline starten</h2>
        {readyVodsQuery.isLoading && <LoadingState message="Lade VODs…" />}
        {readyVodsQuery.error && (
          <ErrorState
            message={readyVodsQuery.error instanceof ApiError ? readyVodsQuery.error.message : "VODs konnten nicht geladen werden."}
          />
        )}
        {readyVodsQuery.data && startableVods.length === 0 && (
          <EmptyState
            title="Keine VODs verfügbar"
            description="Es gibt keine READY VODs ohne aktive Pipeline. Lade zuerst einen VOD im VOD Downloader herunter."
          />
        )}
        {startableVods.length > 0 && (
          <div className="pipeline-start-grid">
            {startableVods.map((vod) => (
              <Card key={vod.id} className="pipeline-start-card">
                <div className="pipeline-start-card__info">
                  <div className="pipeline-start-card__title">{vod.title}</div>
                  <div className="pipeline-start-card__meta">
                    {vod.duration_seconds && formatDuration(vod.duration_seconds)}
                    {vod.duration_seconds && " · "}
                    {vod.twitch_video_id}
                  </div>
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => startMutation.mutate({ source_type: "twitch_vod", source_id: vod.id })}
                  disabled={startMutation.isPending}
                >
                  <Play size={14} />
                  Pipeline starten
                </Button>
              </Card>
            ))}
          </div>
        )}
        {startMutation.error && (
          <ErrorState
            message={startMutation.error instanceof ApiError ? startMutation.error.message : "Pipeline konnte nicht gestartet werden."}
          />
        )}
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Pipeline-Runs</h2>
        {runsQuery.isLoading && <LoadingState message="Lade Pipeline-Runs…" />}
        {runsQuery.error && (
          <ErrorState
            message={runsQuery.error instanceof ApiError ? runsQuery.error.message : "Pipeline-Runs konnten nicht geladen werden."}
          />
        )}
        {runsQuery.data && runs.length === 0 && (
          <EmptyState
            title="Keine Pipeline-Runs"
            description="Starte eine Pipeline für einen READY VOD, um den automatisierten Ablauf zu sehen."
          />
        )}
        {runs.length > 0 && (
          <div className="pipeline-runs-list">
            {runs.map((run) => (
              <PipelineRunCard
                key={run.id}
                run={run}
                vodTitle={vodTitleMap.get(run.source_id)}
                onCancel={() => cancelMutation.mutate(run.id)}
                onRetry={() => retryMutation.mutate(run.id)}
                onDelete={() => deleteMutation.mutate(run.id)}
                cancelPending={cancelMutation.isPending}
                retryPending={retryMutation.isPending}
                deletePending={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </section>

      {hasActiveRuns && (
        <div className="pipeline-polling-hint">
          <Workflow size={14} />
          Aktive Runs werden automatisch aktualisiert.
        </div>
      )}
    </div>
  );
}

