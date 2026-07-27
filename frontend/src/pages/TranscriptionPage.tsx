import { useMemo, useState } from "react";
import {
  FileText,
  Play,
  X,
  RefreshCw,
  Trash2,
  Download,
  AlertCircle,
  Cpu,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatDateTime, formatDuration } from "../utils/format";
import {
  useTranscriptionRuntimeQuery,
  useTranscriptionsQuery,
  useStartTranscriptionMutation,
  useCancelTranscriptionMutation,
  useRetryTranscriptionMutation,
  useDeleteTranscriptionMutation,
  transcriptFileUrl,
} from "../features/mediaProcessing";
import { useVodsQuery } from "../features/vodPipeline";
import type { MediaJob } from "../features/mediaProcessing";

function jobStatusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "READY":
      return { variant: "success", label: "Fertig" };
    case "RUNNING":
      return { variant: "info", label: "Läuft" };
    case "EXPORTING":
      return { variant: "info", label: "Exportiert" };
    case "QUEUED":
      return { variant: "info", label: "Warteschlange" };
    case "WAITING_FOR_DEPENDENCY":
      return { variant: "info", label: "Wartet auf Audio" };
    case "WAITING_FOR_GPU":
      return { variant: "info", label: "Wartet auf GPU" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    default:
      return { variant: "muted", label: status };
  }
}

function isJobActive(status: string): boolean {
  return (
    status === "RUNNING" ||
    status === "EXPORTING" ||
    status === "QUEUED" ||
    status === "WAITING_FOR_DEPENDENCY" ||
    status === "WAITING_FOR_GPU"
  );
}

function phaseLabel(phase?: string | null): string {
  switch (phase) {
    case "LOADING_MODEL":
      return "Modell wird geladen";
    case "DOWNLOADING_MODEL":
      return "Modell wird heruntergeladen";
    case "TRANSCRIBING":
      return "Transkribiert";
    case "TRANSCRIBING_NO_VAD":
      return "Transkribiert (ohne VAD)";
    case "EXPORTING":
      return "Exportiert";
    case "WAITING_FOR_GPU":
      return "Wartet auf GPU";
    case "INSTALLING_DEPENDENCIES":
      return "Installiere Abhängigkeiten";
    default:
      return "";
  }
}

function TranscriptionJobCard({
  job,
  vodTitle,
  onCancel,
  onRetry,
  onDelete,
  cancelPending,
  retryPending,
  deletePending,
}: {
  job: MediaJob;
  vodTitle?: string;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
  cancelPending: boolean;
  retryPending: boolean;
  deletePending: boolean;
}) {
  const status = jobStatusBadge(job.status);
  const active = isJobActive(job.status);
  const progress = job.progress;
  const phase = phaseLabel(progress?.phase);
  const transcript = job.transcript;
  const hasFiles = transcript?.files != null;

  return (
    <Card className="transcription-card">
      <div className="transcription-card__header">
        <div className="transcription-card__title">
          <Badge variant={status.variant}>{status.label}</Badge>
          <span className="transcription-card__vod-title">{vodTitle ?? job.source_id}</span>
        </div>
        <div className="transcription-card__actions">
          {active && (
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={cancelPending}>
              <X size={14} />
              Abbrechen
            </Button>
          )}
          {(job.status === "FAILED" || job.status === "CANCELED") && (
            <Button variant="secondary" size="sm" onClick={onRetry} disabled={retryPending}>
              <RefreshCw size={14} />
              Erneut
            </Button>
          )}
          {!active && (
            <Button variant="danger" size="sm" onClick={onDelete} disabled={deletePending}>
              <Trash2 size={14} />
              Löschen
            </Button>
          )}
        </div>
      </div>

      {active && progress && (
        <div className="transcription-card__progress">
          {phase && <span className="transcription-card__phase">{phase}</span>}
          {progress.percent != null && (
            <div className="transcription-card__progress-bar">
              <div
                className="transcription-card__progress-fill"
                style={{ width: `${Math.min(100, Math.max(0, progress.percent))}%` }}
              />
            </div>
          )}
          {progress.processed_seconds != null && progress.total_seconds != null && (
            <span className="transcription-card__progress-text">
              {formatDuration(progress.processed_seconds)} / {formatDuration(progress.total_seconds)}
            </span>
          )}
        </div>
      )}

      {job.error && (
        <div className="transcription-card__error">
          <AlertCircle size={14} />
          {job.error}
        </div>
      )}

      {job.status === "READY" && transcript && hasFiles && (
        <div className="transcription-card__downloads">
          <span className="transcription-card__downloads-label">Downloads:</span>
          {(["txt", "srt", "vtt", "json"] as const).map((ext) => (
            <a
              key={ext}
              href={transcriptFileUrl(transcript.id, ext)}
              className="transcription-card__download-link"
              download
            >
              <Download size={12} />
              {ext.toUpperCase()}
            </a>
          ))}
        </div>
      )}

      <div className="transcription-card__footer">
        <span className="transcription-card__meta">
          Modell: {job.options?.model ?? "—"}
        </span>
        <span className="transcription-card__meta">
          Sprache: {job.options?.language ?? "—"}
        </span>
        <span className="transcription-card__meta">
          Erstellt: {formatDateTime(job.created_at)}
        </span>
      </div>
    </Card>
  );
}

function RuntimeStatusCard() {
  const query = useTranscriptionRuntimeQuery();
  if (query.isLoading) return <LoadingState message="Lade Transkriptions-Status…" />;
  if (query.error || !query.data) {
    return (
      <ErrorState
        message={query.error instanceof ApiError ? query.error.message : "Status konnte nicht geladen werden."}
      />
    );
  }
  const rt = query.data;
  return (
    <Card className="runtime-status-card">
      <div className="runtime-status-card__header">
        <Cpu size={18} />
        <span className="runtime-status-card__title">Transkriptions-Backend</span>
        <Badge variant={rt.available ? "success" : rt.busy ? "info" : "error"}>
          {rt.available ? "Verfügbar" : rt.busy ? "GPU belegt" : "Nicht verfügbar"}
        </Badge>
      </div>
      <div className="runtime-status-card__details">
        <div className="runtime-status-card__row">
          <span>Modell</span>
          <span>{rt.model}</span>
        </div>
        <div className="runtime-status-card__row">
          <span>Device</span>
          <span>{rt.device}{rt.device_name ? ` (${rt.device_name})` : ""}</span>
        </div>
        <div className="runtime-status-card__row">
          <span>Compute Type</span>
          <span>{rt.compute_type}</span>
        </div>
        <div className="runtime-status-card__row">
          <span>faster-whisper</span>
          {rt.faster_whisper_importable ? (
            <CheckCircle2 size={14} className="runtime-status-card__ok" />
          ) : (
            <XCircle size={14} className="runtime-status-card__bad" />
          )}
        </div>
        <div className="runtime-status-card__row">
          <span>Modell gecacht</span>
          {rt.model_cached ? (
            <CheckCircle2 size={14} className="runtime-status-card__ok" />
          ) : (
            <span className="runtime-status-card__muted">
              {rt.faster_whisper_importable ? "wird beim ersten Lauf heruntergeladen" : "—"}
            </span>
          )}
        </div>
        {rt.device.startsWith("cuda") && (
          <div className="runtime-status-card__row">
            <span>CUDA</span>
            {rt.cuda_available ? (
              <CheckCircle2 size={14} className="runtime-status-card__ok" />
            ) : (
              <XCircle size={14} className="runtime-status-card__bad" />
            )}
          </div>
        )}
        {rt.busy && rt.busy_owner_type && (
          <div className="runtime-status-card__row">
            <span>GPU belegt durch</span>
            <span>{rt.busy_owner_type === "voice_clone" ? "Voice Clone" : "Transkription"}</span>
          </div>
        )}
      </div>
      {rt.reasons.length > 0 && (
        <div className="runtime-status-card__reasons">
          {rt.reasons.map((r, i) => (
            <div key={i} className="runtime-status-card__reason">
              <AlertCircle size={12} />
              {r}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/**
 * Transcription on-demand page.
 *
 * Lets the user pick a READY VOD and start a transcription. Shows the
 * runtime status (faster-whisper availability, GPU state) and a list of
 * all transcription jobs with progress, cancel/retry/delete and
 * download links (TXT, SRT, VTT, JSON).
 */
export function TranscriptionPage() {
  const [selectedVodId, setSelectedVodId] = useState<string>("");
  const [language, setLanguage] = useState<string>("de");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const jobsQuery = useTranscriptionsQuery(undefined, { refetchInterval: 3_000 });
  const readyVodsQuery = useVodsQuery({ status: "READY" });

  const startMutation = useStartTranscriptionMutation();
  const cancelMutation = useCancelTranscriptionMutation();
  const retryMutation = useRetryTranscriptionMutation();
  const deleteMutation = useDeleteTranscriptionMutation();

  const jobs = jobsQuery.data?.transcriptions ?? [];
  const readyVods = readyVodsQuery.data?.vods ?? [];

  const hasActiveJobs = useMemo(() => jobs.some((j) => isJobActive(j.status)), [jobs]);

  const vodTitleMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const v of readyVods) m.set(v.id, v.title);
    for (const j of jobs) if (!m.has(j.source_id)) m.set(j.source_id, j.source_id);
    return m;
  }, [readyVods, jobs]);

  const handleStart = () => {
    if (!selectedVodId) return;
    startMutation.mutate({
      source_type: "twitch_vod",
      source_id: selectedVodId,
      language: language || undefined,
    });
  };

  return (
    <div className="page">
      <section className="page__section">
        <RuntimeStatusCard />
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Neue Transkription starten</h2>
        <Card className="transcription-form-card">
          <div className="transcription-form">
            <label className="transcription-form__field">
              <span className="transcription-form__label">VOD</span>
              <select
                value={selectedVodId}
                onChange={(e) => setSelectedVodId(e.target.value)}
                className="transcription-form__select"
                disabled={readyVods.length === 0}
              >
                <option value="">
                  {readyVods.length === 0 ? "Keine READY VODs verfügbar" : "VOD auswählen…"}
                </option>
                {readyVods.map((vod) => (
                  <option key={vod.id} value={vod.id}>
                    {vod.title} ({vod.duration_seconds ? formatDuration(vod.duration_seconds) : "?"})
                  </option>
                ))}
              </select>
            </label>
            <label className="transcription-form__field">
              <span className="transcription-form__label">Sprache</span>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="transcription-form__select"
              >
                <option value="de">Deutsch</option>
                <option value="en">Englisch</option>
                <option value="auto">Automatisch</option>
              </select>
            </label>
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={!selectedVodId || startMutation.isPending}
            >
              <Play size={14} />
              Transkription starten
            </Button>
          </div>
          {startMutation.error && (
            <ErrorState
              message={startMutation.error instanceof ApiError ? startMutation.error.message : "Transkription konnte nicht gestartet werden."}
            />
          )}
          <p className="transcription-form__hint">
            Falls noch kein Audio-Artefakt existiert, wird es automatisch extrahiert.
          </p>
        </Card>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Transkriptionen</h2>
        {jobsQuery.isLoading && <LoadingState message="Lade Transkriptionen…" />}
        {jobsQuery.error && (
          <ErrorState
            message={jobsQuery.error instanceof ApiError ? jobsQuery.error.message : "Transkriptionen konnten nicht geladen werden."}
          />
        )}
        {jobsQuery.data && jobs.length === 0 && (
          <EmptyState
            title="Keine Transkriptionen"
            description="Starte eine Transkription für einen READY VOD."
          />
        )}
        {jobs.length > 0 && (
          <div className="transcription-list">
            {jobs.map((job) => (
              <TranscriptionJobCard
                key={job.id}
                job={job}
                vodTitle={vodTitleMap.get(job.source_id)}
                onCancel={() => cancelMutation.mutate(job.transcription_id ?? job.id)}
                onRetry={() => retryMutation.mutate(job.transcription_id ?? job.id)}
                onDelete={() => setDeleteTarget(job.transcription_id ?? job.id)}
                cancelPending={cancelMutation.isPending}
                retryPending={retryMutation.isPending}
                deletePending={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </section>

      {hasActiveJobs && (
        <div className="transcription-polling-hint">
          <FileText size={14} />
          Aktive Jobs werden automatisch aktualisiert.
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Transkription löschen"
        description="Möchtest du diese Transkription und alle zugehörigen Dateien (TXT, SRT, VTT, JSON) unwiderruflich löschen?"
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget);
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}

