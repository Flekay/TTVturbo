import { useMemo, useState } from "react";
import {
  FileText,
  X,
  RefreshCw,
  Trash2,
  Download,
  AlertCircle,
  Upload,
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
  useTranscriptionsQuery,
  useUploadTranscriptionMutation,
  useCancelTranscriptionMutation,
  useRetryTranscriptionMutation,
  useDeleteTranscriptionMutation,
  transcriptFileUrl,
} from "../features/mediaProcessing";
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
  onCancel,
  onRetry,
  onDelete,
  cancelPending,
  retryPending,
  deletePending,
}: {
  job: MediaJob;
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
          <span className="transcription-card__vod-title">{job.source_id}</span>
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

/**
 * Transcription on-demand page.
 *
 * Lets the user pick a READY VOD and start a transcription. Shows a
 * list of all transcription jobs with progress, cancel/retry/delete and
 * download links (TXT, SRT, VTT, JSON). Runtime status is surfaced in
 * the topbar status popover.
 */
export function TranscriptionPage() {
  const [language, setLanguage] = useState<string>("de");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const jobsQuery = useTranscriptionsQuery(undefined, { refetchInterval: 3_000 });

  const uploadMutation = useUploadTranscriptionMutation();
  const cancelMutation = useCancelTranscriptionMutation();
  const retryMutation = useRetryTranscriptionMutation();
  const deleteMutation = useDeleteTranscriptionMutation();

  const jobs = jobsQuery.data?.transcriptions ?? [];

  const hasActiveJobs = useMemo(() => jobs.some((j) => isJobActive(j.status)), [jobs]);

  const handleUpload = () => {
    if (!uploadFile) return;
    uploadMutation.mutate(
      { file: uploadFile, language: language || undefined },
      {
        onSuccess: () => setUploadFile(null),
      },
    );
  };

  return (
    <div className="page">
      <section className="page__section">
        <h2 className="page__section-title">Neue Transkription starten</h2>
        <Card className="transcription-form-card">
          <div className="transcription-form">
            <label className="transcription-form__field">
              <span className="transcription-form__label">Datei hochladen</span>
              <input
                type="file"
                accept="video/*,audio/*"
                onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                className="transcription-form__file-input"
                disabled={uploadMutation.isPending}
              />
            </label>
            <label className="transcription-form__field">
              <span className="transcription-form__label">Sprache</span>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="transcription-form__select"
                disabled={uploadMutation.isPending}
              >
                <option value="de">Deutsch</option>
                <option value="en">Englisch</option>
                <option value="auto">Automatisch</option>
              </select>
            </label>
            <Button
              variant="primary"
              onClick={handleUpload}
              disabled={!uploadFile || uploadMutation.isPending}
              loading={uploadMutation.isPending}
            >
              <Upload size={14} />
              Hochladen & transkribieren
            </Button>
          </div>
          {uploadMutation.error && (
            <ErrorState
              message={uploadMutation.error instanceof ApiError ? uploadMutation.error.message : "Upload fehlgeschlagen."}
            />
          )}
          <p className="transcription-form__hint">
            Unabhängig vom VOD Downloader — die Datei wird direkt transkribiert.
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
            description="Lade eine Datei hoch, um eine Transkription zu starten."
          />
        )}
        {jobs.length > 0 && (
          <div className="transcription-list">
            {jobs.map((job) => (
              <TranscriptionJobCard
                key={job.id}
                job={job}
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

