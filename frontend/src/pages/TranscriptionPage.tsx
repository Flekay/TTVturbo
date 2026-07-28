import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FileText,
  X,
  RefreshCw,
  Trash2,
  Download,
  AlertCircle,
  Upload,
  Library,
  Film,
  Music,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { ApiError } from "../api/client";
import { formatBytes, formatDateTime, formatDuration } from "../utils/format";
import {
  useTranscriptionsQuery,
  useUploadTranscriptionMutation,
  useStartTranscriptionMutation,
  useCancelTranscriptionMutation,
  useRetryTranscriptionMutation,
  useDeleteTranscriptionMutation,
  transcriptFileUrl,
  sourceAudioFileUrl,
} from "../features/mediaProcessing";
import { libraryItemFileUrl } from "../features/library/api";
import { useLibraryItemsQuery } from "../features/library/hooks";
import type { MediaJob } from "../features/mediaProcessing";
import { AsrComparisonPanel } from "../features/asrComparison";

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
  const isFileUpload = job.source_type === "file_upload";
  const sourceDetailPath = isFileUpload
    ? `/library/${job.source_id}`
    : `/vod-pipeline/${job.source_id}`;

  return (
    <Card className="transcription-card">
      <div className="transcription-card__header">
        <div className="transcription-card__title">
          <Badge variant={status.variant}>{status.label}</Badge>
          <Link to={sourceDetailPath} className="transcription-card__vod-title" title="Details öffnen">
            {isFileUpload ? <Library size={12} /> : <Film size={12} />}
            {job.source_id}
          </Link>
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
          {isFileUpload && (
            <a
              href={libraryItemFileUrl(job.source_id)}
              className="transcription-card__download-link"
              download
            >
              <Film size={12} />
              Video
            </a>
          )}
          <a
            href={sourceAudioFileUrl(job.source_type, job.source_id)}
            className="transcription-card__download-link"
            download
          >
            <Music size={12} />
            Audio
          </a>
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
          Modell: {job.options?.model ?? "—"} ({job.options?.model_family ?? "whisper"})
        </span>
        <span className="transcription-card__meta">
          Sprache: {job.options?.language ?? "—"}
        </span>
        {job.options?.hotwords && (
          <span className="transcription-card__meta">
            Hotwords: {job.options.hotwords}
          </span>
        )}
        <span className="transcription-card__meta">
          Erstellt: {formatDateTime(job.created_at)}
        </span>
      </div>
    </Card>
  );
}

type SourceMode = "upload" | "library";

/**
 * Transcription on-demand page.
 *
 * Lets the user start a transcription either by uploading a file or by
 * picking an existing item from the Bibliothek. Shows a list of all
 * transcription jobs with progress, cancel/retry/delete and download
 * links (TXT, SRT, VTT, JSON). Runtime status is surfaced in the
 * topbar status popover.
 */
export function TranscriptionPage() {
  const [pageMode, setPageMode] = useState<"transcribe" | "asr-comparison">("transcribe");
  const [mode, setMode] = useState<SourceMode>("library");
  const [language, setLanguage] = useState<string>("de");
  const [modelFamily, setModelFamily] = useState<string>("whisper");
  const [hotwords, setHotwords] = useState<string>("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string>("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const jobsQuery = useTranscriptionsQuery(undefined, { refetchInterval: 3_000 });
  const libraryQuery = useLibraryItemsQuery();

  const uploadMutation = useUploadTranscriptionMutation();
  const startMutation = useStartTranscriptionMutation();
  const cancelMutation = useCancelTranscriptionMutation();
  const retryMutation = useRetryTranscriptionMutation();
  const deleteMutation = useDeleteTranscriptionMutation();

  const jobs = jobsQuery.data?.transcriptions ?? [];

  const hasActiveJobs = useMemo(() => jobs.some((j) => isJobActive(j.status)), [jobs]);

  // Only items that actually have a file on disk are eligible.
  const libraryItems = useMemo(() => {
    const all = libraryQuery.data?.items ?? [];
    return all.filter((it) => it.file_exists !== false);
  }, [libraryQuery.data?.items]);

  const selectedItem = useMemo(
    () => libraryItems.find((it) => it.id === selectedItemId) ?? null,
    [libraryItems, selectedItemId],
  );

  const startPending = uploadMutation.isPending || startMutation.isPending;
  const startError = uploadMutation.error ?? startMutation.error ?? null;

  const handleUpload = () => {
    if (!uploadFile) return;
    uploadMutation.mutate(
      {
        file: uploadFile,
        language: language || undefined,
        model_family: modelFamily || undefined,
        hotwords: hotwords.trim() || undefined,
      },
      {
        onSuccess: () => setUploadFile(null),
      },
    );
  };

  const handleStartFromLibrary = () => {
    if (!selectedItemId) return;
    startMutation.mutate(
      {
        source_type: "file_upload",
        source_id: selectedItemId,
        language: language || undefined,
        model_family: modelFamily || undefined,
        hotwords: hotwords.trim() || undefined,
      },
      {
        onSuccess: () => setSelectedItemId(""),
      },
    );
  };

  return (
    <div className="page">
      <div className="transcription-mode-tabs" role="tablist" aria-label="Seitenmodus">
        <button
          type="button"
          role="tab"
          aria-selected={pageMode === "transcribe"}
          className={`transcription-mode-tabs__btn${pageMode === "transcribe" ? " is-active" : ""}`}
          onClick={() => setPageMode("transcribe")}
        >
          Transkription
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={pageMode === "asr-comparison"}
          className={`transcription-mode-tabs__btn${pageMode === "asr-comparison" ? " is-active" : ""}`}
          onClick={() => setPageMode("asr-comparison")}
        >
          ASR-Vergleich
        </button>
      </div>

      {pageMode === "asr-comparison" ? (
        <AsrComparisonPanel />
      ) : (
        <>
      <section className="page__section">
        <h2 className="page__section-title">Neue Transkription starten</h2>
        <Card className="transcription-form-card">
          <div className="transcription-source-toggle" role="tablist" aria-label="Quelle wählen">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "upload"}
              className={`transcription-source-toggle__btn${mode === "upload" ? " is-active" : ""}`}
              onClick={() => setMode("upload")}
              disabled={startPending}
            >
              <Upload size={14} /> Datei hochladen
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "library"}
              className={`transcription-source-toggle__btn${mode === "library" ? " is-active" : ""}`}
              onClick={() => setMode("library")}
              disabled={startPending}
            >
              <Library size={14} /> Aus Bibliothek
            </button>
          </div>

          <div className="transcription-form">
            {mode === "upload" ? (
              <label className="transcription-form__field">
                <span className="transcription-form__label">Datei hochladen</span>
                <input
                  type="file"
                  accept="video/*,audio/*"
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                  className="transcription-form__file-input"
                  disabled={startPending}
                />
              </label>
            ) : (
              <label className="transcription-form__field">
                <span className="transcription-form__label">Bibliothekseintrag</span>
                {libraryQuery.isLoading ? (
                  <span className="transcription-form__muted">Lade Bibliothek …</span>
                ) : libraryItems.length === 0 ? (
                  <span className="transcription-form__muted">Keine Dateien in der Bibliothek.</span>
                ) : (
                  <select
                    value={selectedItemId}
                    onChange={(e) => setSelectedItemId(e.target.value)}
                    className="transcription-form__select"
                    disabled={startPending}
                  >
                    <option value="">— Eintrag wählen —</option>
                    {libraryItems.map((it) => (
                      <option key={it.id} value={it.id}>
                        {it.title || it.file_name}
                        {it.duration_seconds != null ? ` (${formatDuration(it.duration_seconds)})` : ""}
                        {it.file_size_bytes != null ? ` · ${formatBytes(it.file_size_bytes)}` : ""}
                      </option>
                    ))}
                  </select>
                )}
              </label>
            )}
            <label className="transcription-form__field">
              <span className="transcription-form__label">Sprache</span>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="transcription-form__select"
                disabled={startPending}
              >
                <option value="de">Deutsch</option>
                <option value="en">Englisch</option>
                <option value="auto">Automatisch</option>
              </select>
            </label>
            <label className="transcription-form__field">
              <span className="transcription-form__label">Modell</span>
              <select
                value={modelFamily}
                onChange={(e) => setModelFamily(e.target.value)}
                className="transcription-form__select"
                disabled={startPending}
              >
                <option value="whisper">Whisper (large-v3)</option>
                <option value="parakeet">NVIDIA Parakeet TDT 0.6B v3</option>
                <option value="canary">NVIDIA Canary 1B v2</option>
              </select>
            </label>
            <label className="transcription-form__field">
              <span className="transcription-form__label">Hotwords (kontextbezogenes Wörterbuch)</span>
              <input
                type="text"
                value={hotwords}
                onChange={(e) => setHotwords(e.target.value)}
                className="transcription-form__input"
                disabled={startPending || modelFamily !== "whisper"}
                placeholder={modelFamily !== "whisper" ? "Nur für Whisper verfügbar" : "z.B. Drake, Trick, Gott"}
              />
            </label>
            {mode === "upload" ? (
              <Button
                variant="primary"
                onClick={handleUpload}
                disabled={!uploadFile || startPending}
                loading={uploadMutation.isPending}
              >
                <Upload size={14} />
                Hochladen & transkribieren
              </Button>
            ) : (
              <Button
                variant="primary"
                onClick={handleStartFromLibrary}
                disabled={!selectedItemId || startPending}
                loading={startMutation.isPending}
              >
                <FileText size={14} />
                Transkribieren
              </Button>
            )}
          </div>
          {startError && (
            <ErrorState
              message={startError instanceof ApiError ? startError.message : "Transkription konnte nicht gestartet werden."}
            />
          )}
          {mode === "upload" ? (
            <p className="transcription-form__hint">
              Unabhängig vom VOD Downloader — die Datei wird direkt transkribiert.
            </p>
          ) : (
            <p className="transcription-form__hint">
              Wähle eine Datei aus der Bibliothek. Heruntergeladene VODs und Uploads stehen zur Verfügung.
            </p>
          )}
          {mode === "library" && selectedItem && (
            <p className="transcription-form__hint">
              Auswahl: <strong>{selectedItem.title || selectedItem.file_name}</strong>
              {selectedItem.duration_seconds != null
                ? ` · ${formatDuration(selectedItem.duration_seconds)}`
                : ""}
              {selectedItem.file_size_bytes != null
                ? ` · ${formatBytes(selectedItem.file_size_bytes)}`
                : ""}
            </p>
          )}
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
            description="Lade eine Datei hoch oder wähle einen Eintrag aus der Bibliothek, um eine Transkription zu starten."
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
        </>
      )}
    </div>
  );
}

