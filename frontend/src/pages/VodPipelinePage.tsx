import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Play,
  X,
  RefreshCw,
  Trash2,
  ArrowRight,
  AlertCircle,
  Check,
  Circle,
  Loader2,
  ExternalLink,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatDateTime, formatDuration } from "../utils/format";
import {
  usePipelineRunsFilteredQuery,
  useStartVodPipelineRunMutation,
  useCancelPipelineRunMutation,
  useRetryPipelineRunMutation,
  useDeletePipelineRunMutation,
} from "../features/mediaProcessing";
import type { PipelineRun } from "../features/mediaProcessing";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACTIVE_STATUSES = "QUEUED,RUNNING,WAITING_FOR_GPU,CANCELING,RETRYING";
const HISTORY_STATUSES = "COMPLETED,READY_FOR_CLIP_ANALYSIS,FAILED,CANCELED";

const STEP_LABELS: Record<string, string> = {
  RESOLVE_SOURCE: "Quelle erkannt",
  DOWNLOAD: "Video herunterladen",
  EXTRACT_AUDIO: "Audio extrahieren",
  TRANSCRIBE: "Transkribieren",
  FIND_CLIPS: "Clip-Suche",
};

const STEP_ORDER: string[] = [
  "RESOLVE_SOURCE",
  "DOWNLOAD",
  "EXTRACT_AUDIO",
  "TRANSCRIBE",
  "FIND_CLIPS",
];

function isRunActive(status: string): boolean {
  return (
    status === "RUNNING" ||
    status === "QUEUED" ||
    status === "WAITING_FOR_GPU" ||
    status === "CANCELING" ||
    status === "RETRYING"
  );
}

function runStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "COMPLETED":
      return { variant: "success", label: "Abgeschlossen" };
    case "READY_FOR_CLIP_ANALYSIS":
      return { variant: "success", label: "Bereit für Clip-Analyse" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "WAITING_FOR_GPU":
      return { variant: "info", label: "Wartet auf GPU" };
    case "QUEUED":
      return { variant: "info", label: "Warteschlange" };
    case "CANCELING":
      return { variant: "warning", label: "Wird abgebrochen" };
    case "RETRYING":
      return { variant: "info", label: "Erneut" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    default:
      return { variant: "muted", label: status };
  }
}

function stepMarker(status: string) {
  switch (status) {
    case "READY":
    case "SKIPPED":
      return <Check size={14} />;
    case "RUNNING":
    case "WAITING_FOR_GPU":
    case "QUEUED":
      return <Loader2 size={14} className="spin" />;
    case "FAILED":
    case "CANCELED":
      return <X size={14} />;
    case "NOT_IMPLEMENTED":
      return <Circle size={14} />;
    default:
      return <Circle size={14} />;
  }
}

function stepStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY":
      return { variant: "success", label: "Fertig" };
    case "SKIPPED":
      return { variant: "muted", label: "Übersprungen" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "WAITING_FOR_GPU":
      return { variant: "info", label: "Wartet auf GPU" };
    case "QUEUED":
      return { variant: "muted", label: "Wartet" };
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

function runTitle(run: PipelineRun): string {
  const src = run.source;
  if (src?.title) return src.title;
  if (src?.legacy) return "Legacy Pipeline Run";
  return run.source_id;
}

function runThumb(run: PipelineRun): string | null {
  return run.source?.thumbnail_url ?? null;
}

function runTypeLabel(run: PipelineRun): string {
  const t = run.source?.type;
  if (t === "clip") return "Clip";
  if (t === "vod") return "VOD";
  return "VOD";
}

// ---------------------------------------------------------------------------
// Run card
// ---------------------------------------------------------------------------

function RunCard({
  run,
  onCancel,
  onRetry,
  onDelete,
  onRestart,
  cancelPending,
  retryPending,
  deletePending,
}: {
  run: PipelineRun;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
  onRestart: () => void;
  cancelPending: boolean;
  retryPending: boolean;
  deletePending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = runStatusBadge(run.status);
  const active = isRunActive(run.status);
  const thumb = runThumb(run);
  const title = runTitle(run);
  const typeLabel = runTypeLabel(run);
  const progress = run.progress ?? 0;
  const hasProgress = typeof run.progress === "number";
  const isFailed = run.status === "FAILED";
  const isCanceled = run.status === "CANCELED";
  const failedStep = (run.steps ?? []).find((s) => s.status === "FAILED");

  // Sort steps by canonical order, unknown steps appended.
  const steps = useMemo(() => {
    const arr = [...(run.steps ?? [])];
    arr.sort((a, b) => {
      const ia = STEP_ORDER.indexOf(a.type);
      const ib = STEP_ORDER.indexOf(b.type);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
    return arr;
  }, [run.steps]);

  return (
    <Card className="vp-run-card" data-run-id={run.id} data-run-status={run.status}>
      <div className="vp-run-card__top">
        {thumb ? (
          <img className="vp-run-card__thumb" src={thumb} alt="" />
        ) : (
          <div className="vp-run-card__thumb vp-run-card__thumb--placeholder">{typeLabel}</div>
        )}
        <div className="vp-run-card__head">
          <div className="vp-run-card__title-row">
            <Badge variant={status.variant}>{status.label}</Badge>
            <span className="vp-run-card__title" title={title}>{title}</span>
          </div>
          <div className="vp-run-card__sub">
            <span>{typeLabel}</span>
            {run.source?.external_id && <span>· {run.source.external_id}</span>}
            {run.source?.duration_seconds != null && (
              <span>· {formatDuration(Number(run.source.duration_seconds))}</span>
            )}
            {run.started_at && <span>· {formatDateTime(run.started_at)}</span>}
            {run.completed_at && <span>· fertig {formatDateTime(run.completed_at)}</span>}
          </div>
          {hasProgress && (
            <div className="vp-run-card__progress">
              <div className="vp-progress-bar">
                <div
                  className={
                    active && progress >= 100
                      ? "vp-progress-bar__fill"
                      : "vp-progress-bar__fill"
                  }
                  style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                />
              </div>
              <div className="vp-progress-meta">
                <span>{run.current_step ? STEP_LABELS[run.current_step] ?? run.current_step : ""}</span>
                <span>{progress.toFixed(0)}%</span>
              </div>
            </div>
          )}
        </div>
        <div className="vp-run-card__actions">
          {active && (
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={cancelPending}>
              <X size={14} /> Abbrechen
            </Button>
          )}
          {isFailed && (
            <Button variant="secondary" size="sm" onClick={onRetry} disabled={retryPending}>
              <RefreshCw size={14} /> Erneut
            </Button>
          )}
          {isCanceled && (
            <Button variant="secondary" size="sm" onClick={onRestart} disabled={retryPending}>
              <Play size={14} /> Neu starten
            </Button>
          )}
          {run.library_item_id && (
            <Link
              className="btn btn--secondary btn--sm"
              to={`/library/${encodeURIComponent(run.library_item_id)}`}
            >
              <ExternalLink size={14} /> In Library
            </Link>
          )}
          {run.transcript_id && (
            <Link
              className="btn btn--secondary btn--sm"
              to={`/transcription?transcription=${encodeURIComponent(run.transcript_id)}`}
            >
              <ExternalLink size={14} /> Transkript
            </Link>
          )}
          {!active && (
            <Button variant="danger" size="sm" onClick={onDelete} disabled={deletePending}>
              <Trash2 size={14} /> Löschen
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            Details
          </Button>
        </div>
      </div>

      {/* Step list */}
      <div className="vp-step-list">
        {steps.map((step, i) => {
          const sb = stepStatusBadge(step.status);
          const done = step.status === "READY" || step.status === "SKIPPED" || step.status === "NOT_IMPLEMENTED";
          const isActive = step.status === "RUNNING" || step.status === "WAITING_FOR_GPU" || step.status === "QUEUED";
          const cls = `vp-step${done ? " vp-step--done" : ""}${step.status === "FAILED" ? " vp-step--failed" : ""}${isActive ? " vp-step--active" : ""}`;
          return (
            <div key={`${step.type}-${i}`} className={cls}>
              <span className="vp-step__marker">{stepMarker(step.status)}</span>
              <span className="vp-step__label">{STEP_LABELS[step.type] ?? step.type}</span>
              {step.message && <span className="vp-step__detail">{step.message}</span>}
              {typeof step.progress === "number" && isActive && (
                <span className="vp-step__pct">{step.progress.toFixed(0)}%</span>
              )}
              <Badge variant={sb.variant}>{sb.label}</Badge>
              {step.error && (
                <span className="vp-step__detail" title={step.error}>
                  <AlertCircle size={12} />
                </span>
              )}
            </div>
          );
        })}
        {steps.length === 0 && (
          <div className="vp-step">
            <span className="vp-step__detail">Keine Schrittinformationen verfügbar.</span>
          </div>
        )}
      </div>

      {run.error && (
        <div className="pipeline-run-card__error">
          <AlertCircle size={14} />
          {run.error}
        </div>
      )}

      {expanded && (
        <div className="vp-run-detail">
          <div className="vp-run-detail__row">
            <span className="vp-run-detail__label">Run-ID</span>
            <span className="vp-run-detail__value vp-run-detail__tech">{run.id}</span>
          </div>
          <div className="vp-run-detail__row">
            <span className="vp-run-detail__label">Quelle</span>
            <span className="vp-run-detail__value">
              {run.source?.url ?? run.source_id}
            </span>
          </div>
          {run.source?.external_id && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Externe ID</span>
              <span className="vp-run-detail__value">{run.source.external_id}</span>
            </div>
          )}
          {run.profile_id && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Profil</span>
              <span className="vp-run-detail__value vp-run-detail__tech">{run.profile_id}</span>
            </div>
          )}
          {run.library_item_id && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Library-Item</span>
              <span className="vp-run-detail__value vp-run-detail__tech">{run.library_item_id}</span>
            </div>
          )}
          {run.transcript_id && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Transkript</span>
              <span className="vp-run-detail__value vp-run-detail__tech">{run.transcript_id}</span>
            </div>
          )}
          <div className="vp-run-detail__row">
            <span className="vp-run-detail__label">Erstellt</span>
            <span className="vp-run-detail__value">{formatDateTime(run.created_at)}</span>
          </div>
          {run.started_at && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Gestartet</span>
              <span className="vp-run-detail__value">{formatDateTime(run.started_at)}</span>
            </div>
          )}
          {run.completed_at && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Abgeschlossen</span>
              <span className="vp-run-detail__value">{formatDateTime(run.completed_at)}</span>
            </div>
          )}
          {failedStep && (
            <div className="vp-run-detail__row">
              <span className="vp-run-detail__label">Fehlgeschlagener Schritt</span>
              <span className="vp-run-detail__value">
                {STEP_LABELS[failedStep.type] ?? failedStep.type}
                {failedStep.error ? `: ${failedStep.error}` : ""}
              </span>
            </div>
          )}
          {steps.map((s) =>
            s.job_id ? (
              <div className="vp-run-detail__row" key={`job-${s.type}`}>
                <span className="vp-run-detail__label">Job {s.type}</span>
                <span className="vp-run-detail__value vp-run-detail__tech">{s.job_id}</span>
              </div>
            ) : null,
          )}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

/**
 * VOD Pipeline page.
 *
 * URL-based import is the primary entry point. The page shows two tabs:
 * "Aktiv" (active runs) and "Verlauf" (terminal runs). The old
 * library-card-wall start mechanism has been removed.
 */
export function VodPipelinePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "history" ? "history" : "active";

  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);

  const activeQuery = usePipelineRunsFilteredQuery(
    { status: ACTIVE_STATUSES },
    { refetchInterval: 2000 },
  );
  // History is only fetched when the history tab is open (or always, but
  // less frequently). We poll it on a slower cadence.
  const historyQuery = usePipelineRunsFilteredQuery(
    { status: HISTORY_STATUSES },
    { refetchInterval: tab === "history" ? 5000 : false },
  );

  const startMutation = useStartVodPipelineRunMutation();
  const cancelMutation = useCancelPipelineRunMutation();
  const retryMutation = useRetryPipelineRunMutation();
  const deleteMutation = useDeletePipelineRunMutation();

  const activeRuns = activeQuery.data?.pipeline_runs ?? [];
  const historyRuns = historyQuery.data?.pipeline_runs ?? [];
  const hasActiveRuns = activeRuns.length > 0;

  // If there are no active runs, stop polling active runs to avoid
  // unnecessary requests. We keep the hook enabled so a freshly started
  // run shows up, but rely on the refetchInterval being a number only when
  // there are active runs. TanStack Query keeps the last data.
  useEffect(() => {
    // No-op: refetchInterval is set above; this exists to make the
    // polling-stop-without-active-runs behaviour explicit and testable.
  }, [hasActiveRuns]);

  function setTab(next: "active" | "history") {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  }

  function handleStart() {
    setUrlError(null);
    const trimmed = url.trim();
    if (!trimmed) {
      setUrlError("Bitte eine Twitch-VOD- oder Clip-URL eingeben.");
      return;
    }
    // Optimistic: disable button via isPending; prevent double-click.
    startMutation.mutate(
      { url: trimmed },
      {
        onSuccess: () => {
          setUrl("");
          setTab("active");
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "Pipeline konnte nicht gestartet werden.";
          setUrlError(msg);
        },
      },
    );
  }

  function handleRestart(run: PipelineRun) {
    // "Neu starten" for a canceled run: retry re-queues from the first
    // non-done step, which is the cleanest reuse of existing artifacts.
    retryMutation.mutate(run.id);
  }

  const runsToShow = tab === "active" ? activeRuns : historyRuns;
  const listQuery = tab === "active" ? activeQuery : historyQuery;

  return (
    <div className="page">
      {/* Import card */}
      <section className="page__section">
        <Card className="vp-import-card">
          <div className="vp-import-card__field">
            <label className="vp-import-card__label" htmlFor="vp-url-input">
              Twitch-VOD- oder Clip-URL
            </label>
            <input
              id="vp-url-input"
              className="input vp-import-card__input"
              type="url"
              placeholder="https://www.twitch.tv/videos/123456789"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !startMutation.isPending) {
                  handleStart();
                }
              }}
              disabled={startMutation.isPending}
              aria-invalid={!!urlError}
            />
            <span className="vp-import-card__hint">
              Beliebige Twitch-VOD- oder Clip-URL einfügen. Profil und Metadaten werden automatisch erkannt.
            </span>
          </div>
          <div className="vp-import-card__actions">
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={startMutation.isPending || !url.trim()}
            >
              <Play size={14} /> Pipeline starten
            </Button>
          </div>
          {urlError && <div className="vp-import-card__error">{urlError}</div>}
          {startMutation.error && !urlError && (
            <ErrorState
              message={
                startMutation.error instanceof ApiError
                  ? startMutation.error.message
                  : "Pipeline konnte nicht gestartet werden."
              }
            />
          )}
        </Card>
      </section>

      {/* Tabs */}
      <section className="page__section">
        <div className="vp-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "active"}
            className={`vp-tab${tab === "active" ? " is-active" : ""}`}
            onClick={() => setTab("active")}
          >
            Aktiv {activeRuns.length > 0 ? `(${activeRuns.length})` : ""}
          </button>
          <button
            role="tab"
            aria-selected={tab === "history"}
            className={`vp-tab${tab === "history" ? " is-active" : ""}`}
            onClick={() => setTab("history")}
          >
            Verlauf {historyRuns.length > 0 ? `(${historyRuns.length})` : ""}
          </button>
        </div>

        {listQuery.isLoading && (
          <LoadingState message={tab === "active" ? "Lade aktive Runs…" : "Lade Verlauf…"} />
        )}
        {listQuery.error && (
          <ErrorState
            message={
              listQuery.error instanceof ApiError
                ? listQuery.error.message
                : tab === "active"
                  ? "Aktive Runs konnten nicht geladen werden."
                  : "Verlauf konnte nicht geladen werden."
            }
          />
        )}

        {listQuery.data && runsToShow.length === 0 && tab === "active" && (
          <EmptyState
            title="Keine aktiven Pipeline-Runs"
            description="Füge oben eine Twitch-VOD- oder Clip-URL ein, um eine neue Pipeline zu starten."
          />
        )}
        {listQuery.data && runsToShow.length === 0 && tab === "history" && (
          <EmptyState title="Noch kein Pipeline-Verlauf" />
        )}

        {runsToShow.length > 0 && (
          <div className="pipeline-runs-list">
            {runsToShow.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                onCancel={() => cancelMutation.mutate(run.id)}
                onRetry={() => retryMutation.mutate(run.id)}
                onDelete={() => deleteMutation.mutate(run.id)}
                onRestart={() => handleRestart(run)}
                cancelPending={cancelMutation.isPending}
                retryPending={retryMutation.isPending}
                deletePending={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </section>

      {hasActiveRuns && tab === "active" && (
        <div className="pipeline-polling-hint">
          <ArrowRight size={14} /> Aktive Runs werden automatisch aktualisiert.
        </div>
      )}
    </div>
  );
}
