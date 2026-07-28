import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  ClipboardList,
  Eye,
  FileText,
  RotateCcw,
  Save,
  X,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import { formatDateTime, formatDuration } from "../../utils/format";
import {
  useTranscriptViewQuery,
  useSaveCorrectionsMutation,
  useResetSegmentCorrectionMutation,
  useResetAllCorrectionsMutation,
  useTranscriptionsQuery,
  transcriptFileUrl,
  sourceAudioFileUrl,
} from "../mediaProcessing";
import { useLibraryItemsQuery } from "../library/hooks";
import { libraryItemFileUrl } from "../library/api";
import type {
  MediaJob,
  TranscriptSegment,
  TranscriptView,
  RevisionConflictDetail,
  SegmentCorrectionInput,
} from "../mediaProcessing";

/**
 * Editable transcript editor.
 *
 * Lets the user pick a READY transcription for a library item, view the
 * raw ASR text alongside an editable correction per segment, preview the
 * effective transcript (raw / corrected toggle), save corrections as a
 * batch with optimistic concurrency, reset a single segment or all
 * corrections, and seek the player by clicking a segment timestamp.
 *
 * The original ``raw_text`` is always visible and never editable.
 */

interface DraftSegment {
  id: string;
  raw_text: string;
  // The local draft correction. ``undefined`` means "unchanged from
  // server"; ``null`` means "explicitly cleared"; a string means
  // "edited".
  draft: string | null | undefined;
  server: string | null;
}

function isDraftDirty(draft: DraftSegment): boolean {
  return draft.draft !== undefined && draft.draft !== draft.server;
}

function effectiveSegmentText(draft: DraftSegment): string {
  if (draft.draft !== undefined && draft.draft !== null && draft.draft.trim() !== "") {
    return draft.draft;
  }
  if (draft.server !== null && draft.server.trim() !== "") {
    return draft.server;
  }
  return draft.raw_text;
}

function formatTimestamp(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  const ms = Math.round((s - Math.floor(s)) * 1000);
  return `${m.toString().padStart(2, "0")}:${r.toString().padStart(2, "0")}.${ms
    .toString()
    .padStart(3, "0")}`;
}

function correctionStatusBadge(status: string | undefined): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "CORRECTED":
      return { variant: "success", label: "Korrigiert" };
    case "REVIEWED":
      return { variant: "info", label: "Geprüft" };
    case "RAW":
    default:
      return { variant: "muted", label: "Original" };
  }
}

function isRevisionConflictError(err: unknown): err is ApiError & { details: { detail: RevisionConflictDetail } } {
  if (!(err instanceof ApiError) || err.status !== 409 || !err.details) return false;
  const details = err.details as { detail?: unknown };
  return (
    !!details.detail &&
    typeof details.detail === "object" &&
    (details.detail as { code?: unknown }).code === "revision_conflict"
  );
}

export function TranscriptEditor() {
  const jobsQuery = useTranscriptionsQuery(undefined, { refetchInterval: false });
  const libraryQuery = useLibraryItemsQuery();

  const readyJobs = useMemo(() => {
    const all = jobsQuery.data?.transcriptions ?? [];
    return all.filter(
      (j) => j.status === "READY" && j.transcription_id && j.transcript?.files,
    );
  }, [jobsQuery.data?.transcriptions]);

  const libraryItems = useMemo(() => {
    const all = libraryQuery.data?.items ?? [];
    return all.filter((it) => it.file_exists !== false);
  }, [libraryQuery.data?.items]);

  const [selectedTranscriptionId, setSelectedTranscriptionId] = useState<string>("");
  const [previewMode, setPreviewMode] = useState<"effective" | "raw" | "corrected">("effective");

  // Reset selection when the list changes and the current selection is
  // no longer present.
  useEffect(() => {
    if (selectedTranscriptionId && !readyJobs.some((j) => j.transcription_id === selectedTranscriptionId)) {
      setSelectedTranscriptionId("");
    }
  }, [readyJobs, selectedTranscriptionId]);

  const selectedJob = useMemo(
    () => readyJobs.find((j) => j.transcription_id === selectedTranscriptionId) ?? null,
    [readyJobs, selectedTranscriptionId],
  );

  return (
    <section className="page__section">
      <h2 className="page__section-title">Transkript korrigieren</h2>
      <Card className="transcript-editor__select-card">
        <label className="transcription-form__field">
          <span className="transcription-form__label">Transkription</span>
          {jobsQuery.isLoading ? (
            <span className="transcription-form__muted">Lade Transkriptionen …</span>
          ) : readyJobs.length === 0 ? (
            <span className="transcription-form__muted">Keine fertigen Transkriptionen vorhanden.</span>
          ) : (
            <select
              value={selectedTranscriptionId}
              onChange={(e) => setSelectedTranscriptionId(e.target.value)}
              className="transcription-form__select"
            >
              <option value="">— Transkription wählen —</option>
              {readyJobs.map((j) => {
                const item = libraryItems.find((it) => it.id === j.source_id);
                const label = item?.title || item?.file_name || j.source_id;
                return (
                  <option key={j.transcription_id} value={j.transcription_id as string}>
                    {label} · {j.options?.model ?? "—"} · {formatDateTime(j.created_at)}
                  </option>
                );
              })}
            </select>
          )}
        </label>
        {jobsQuery.error && (
          <ErrorState
            message={
              jobsQuery.error instanceof ApiError
                ? jobsQuery.error.message
                : "Transkriptionen konnten nicht geladen werden."
            }
          />
        )}
      </Card>

      {selectedJob && selectedTranscriptionId && (
        <TranscriptEditorBody
          transcriptionId={selectedTranscriptionId}
          job={selectedJob}
          previewMode={previewMode}
          onPreviewModeChange={setPreviewMode}
        />
      )}
      {!selectedTranscriptionId && readyJobs.length > 0 && (
        <EmptyState
          title="Kein Transkript ausgewählt"
          description="Wähle oben eine fertige Transkription aus, um Segmente zu korrigieren."
        />
      )}
    </section>
  );
}

interface TranscriptEditorBodyProps {
  transcriptionId: string;
  job: MediaJob;
  previewMode: "effective" | "raw" | "corrected";
  onPreviewModeChange: (mode: "effective" | "raw" | "corrected") => void;
}

function TranscriptEditorBody({
  transcriptionId,
  job,
  previewMode,
  onPreviewModeChange,
}: TranscriptEditorBodyProps) {
  const viewQuery = useTranscriptViewQuery(transcriptionId);
  const saveMutation = useSaveCorrectionsMutation();
  const resetSegmentMutation = useResetSegmentCorrectionMutation();
  const resetAllMutation = useResetAllCorrectionsMutation();

  const videoRef = useRef<HTMLVideoElement>(null);
  const [drafts, setDrafts] = useState<Record<string, DraftSegment>>({});
  const [confirmResetAll, setConfirmResetAll] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState<null | (() => void)>(null);

  const transcript: TranscriptView | null = viewQuery.data ?? null;
  const serverRevision = transcript?.revision ?? null;

  // Rebuild drafts whenever the server transcript changes (load or after
  // a successful save/reset). Drafts always start as "unchanged".
  useEffect(() => {
    if (!transcript) return;
    const next: Record<string, DraftSegment> = {};
    for (const seg of transcript.segments) {
      next[seg.id] = {
        id: seg.id,
        raw_text: seg.raw_text,
        server: seg.corrected_text ?? null,
        draft: undefined,
      };
    }
    setDrafts(next);
  }, [transcript]);

  const dirtyCount = useMemo(
    () => Object.values(drafts).filter(isDraftDirty).length,
    [drafts],
  );
  const hasDirty = dirtyCount > 0;

  const setDraft = useCallback((segmentId: string, value: string) => {
    setDrafts((prev) => {
      const cur = prev[segmentId];
      if (!cur) return prev;
      return { ...prev, [segmentId]: { ...cur, draft: value } };
    });
  }, []);

  const clearDraft = useCallback((segmentId: string) => {
    setDrafts((prev) => {
      const cur = prev[segmentId];
      if (!cur) return prev;
      // Reset local draft to the server value (no-op marker).
      return { ...prev, [segmentId]: { ...cur, draft: undefined } };
    });
  }, []);

  // Ctrl+S saves the current drafts.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        if (!hasDirty || saveMutation.isPending) return;
        e.preventDefault();
        void doSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasDirty, drafts, saveMutation.isPending, serverRevision]);

  // Warn before navigating away with unsaved drafts.
  useEffect(() => {
    if (!hasDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasDirty]);

  const doSave = useCallback(async () => {
    if (!transcript || serverRevision == null) return;
    const updates: SegmentCorrectionInput[] = [];
    for (const seg of transcript.segments) {
      const draft = drafts[seg.id];
      if (!draft || !isDraftDirty(draft)) continue;
      const value = draft.draft ?? null;
      updates.push({ segment_id: seg.id, corrected_text: value });
    }
    if (updates.length === 0) return;
    saveMutation.mutate(
      { transcriptionId, request: { expected_revision: serverRevision, segments: updates } },
      {
        onError: () => {
          /* error surfaced via mutation state */
        },
      },
    );
  }, [transcript, drafts, serverRevision, saveMutation, transcriptionId]);

  const doResetSegment = useCallback(
    (segmentId: string) => {
      if (!transcript) return;
      // Optimistically clear the local draft so the UI feels instant.
      clearDraft(segmentId);
      resetSegmentMutation.mutate({ transcriptionId, segmentId });
    },
    [transcript, clearDraft, resetSegmentMutation, transcriptionId],
  );

  const doResetAll = useCallback(() => {
    if (!transcript) return;
    resetAllMutation.mutate(transcriptionId);
    setConfirmResetAll(false);
  }, [transcript, resetAllMutation, transcriptionId]);

  const discardAllDrafts = useCallback(() => {
    setDrafts((prev) => {
      const next: Record<string, DraftSegment> = {};
      for (const [id, d] of Object.entries(prev)) {
        next[id] = { ...d, draft: undefined };
      }
      return next;
    });
  }, []);

  const requestDiscardOrNavigate = useCallback(
    (action: () => void) => {
      if (hasDirty) {
        setConfirmDiscard(() => () => {
          discardAllDrafts();
          action();
        });
      } else {
        action();
      }
    },
    [hasDirty, discardAllDrafts],
  );

  // Effective preview text computed from local drafts.
  const previewText = useMemo(() => {
    if (!transcript) return "";
    if (previewMode === "raw") {
      return transcript.segments.map((s) => s.raw_text).join(" ").trim();
    }
    if (previewMode === "corrected") {
      return transcript.segments
        .map((s) => s.corrected_text ?? s.raw_text)
        .join(" ")
        .trim();
    }
    // effective
    return transcript.segments
      .map((s) => {
        const draft = drafts[s.id];
        return draft ? effectiveSegmentText(draft) : s.corrected_text ?? s.raw_text;
      })
      .join(" ")
      .trim();
  }, [transcript, drafts, previewMode]);

  const seekTo = useCallback((start: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = start;
    void video.play();
  }, []);

  const videoUrl = useMemo(() => {
    if (job.source_type === "file_upload") return libraryItemFileUrl(job.source_id);
    return sourceAudioFileUrl(job.source_type, job.source_id);
  }, [job]);

  const status = correctionStatusBadge(transcript?.correction_status);
  const saveError = saveMutation.error;
  const isConflict = isRevisionConflictError(saveError);

  return (
    <div className="transcript-editor">
      {/* Media metadata header */}
      <Card className="transcript-editor__meta-card">
        <div className="transcript-editor__meta-header">
          <Badge variant={status.variant}>{status.label}</Badge>
          <span className="transcript-editor__meta-title">
            {job.options?.model ?? "—"} ({job.options?.model_family ?? "whisper"})
          </span>
          <span className="transcript-editor__meta-rev">Revision {transcript?.revision ?? "—"}</span>
        </div>
        <div className="transcript-editor__meta-rows">
          <div className="transcript-editor__meta-row">
            <span>Quelle</span>
            <span>{job.source_type === "file_upload" ? "Bibliothek" : "Twitch VOD"}</span>
          </div>
          {transcript?.duration_seconds != null && (
            <div className="transcript-editor__meta-row">
              <span>Dauer</span>
              <span>{formatDuration(transcript.duration_seconds)}</span>
            </div>
          )}
          <div className="transcript-editor__meta-row">
            <span>Erstellt</span>
            <span>{transcript ? formatDateTime(transcript.created_at) : "—"}</span>
          </div>
          <div className="transcript-editor__meta-row">
            <span>Transkriptionsmodell</span>
            <span>{transcript?.engine?.model ?? job.options?.model ?? "—"}</span>
          </div>
        </div>
      </Card>

      {/* Player + preview */}
      <div className="transcript-editor__player-row">
        <div className="transcript-editor__video-wrap">
          <video
            ref={videoRef}
            src={videoUrl}
            controls
            preload="metadata"
            className="transcript-editor__video"
          />
        </div>
        <Card className="transcript-editor__preview-card">
          <div className="transcript-editor__preview-header">
            <span className="transcript-editor__preview-title">
              <Eye size={14} />
              Effektives Transkript
            </span>
            <div className="transcript-editor__preview-toggle" role="tablist" aria-label="Vorschau-Modus">
              {(["effective", "raw", "corrected"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={previewMode === m}
                  className={`transcript-editor__preview-btn${
                    previewMode === m ? " is-active" : ""
                  }`}
                  onClick={() => onPreviewModeChange(m)}
                >
                  {m === "effective" ? "Effektiv" : m === "raw" ? "Raw" : "Korrigiert"}
                </button>
              ))}
            </div>
          </div>
          <div className="transcript-editor__preview-text">
            {viewQuery.isLoading
              ? "Lade Transkript …"
              : previewText || "Kein Text vorhanden."}
          </div>
        </Card>
      </div>

      {/* Actions */}
      <div className="transcript-editor__actions">
        <Button
          variant="primary"
          onClick={doSave}
          disabled={!hasDirty || saveMutation.isPending}
          loading={saveMutation.isPending}
        >
          <Save size={14} />
          Änderungen speichern
          {hasDirty ? ` (${dirtyCount})` : ""}
        </Button>
        <Button
          variant="secondary"
          onClick={() => requestDiscardOrNavigate(() => {})}
          disabled={!hasDirty}
        >
          <X size={14} />
          Änderungen verwerfen
        </Button>
        <Button
          variant="secondary"
          onClick={() => setConfirmResetAll(true)}
          disabled={resetAllMutation.isPending || (transcript?.correction_status ?? "RAW") === "RAW"}
        >
          <RotateCcw size={14} />
          Alle Korrekturen zurücksetzen
        </Button>
        <a
          href={transcriptFileUrl(transcriptionId, "json")}
          className="transcript-editor__download-link"
          download
        >
          <FileText size={14} />
          JSON
        </a>
      </div>

      {hasDirty && (
        <div className="transcript-editor__dirty-hint">
          <AlertCircle size={14} />
          {dirtyCount} ungespeicherte {dirtyCount === 1 ? "Änderung" : "Änderungen"} · Ctrl+S speichert.
        </div>
      )}

      {saveError && (
        <ErrorState
          message={
            isConflict
              ? `Revision Conflict: Server hat Revision ${saveError.details.detail.current_revision}, erwartet ${serverRevision}. Bitte neu laden.`
              : saveError instanceof ApiError
                ? saveError.message
                : "Speichern fehlgeschlagen."
          }
        />
      )}
      {isConflict && (
        <Button
          variant="secondary"
          onClick={() => {
            // Reload the server transcript (drops drafts).
            viewQuery.refetch();
          }}
        >
          <RotateCcw size={14} />
          Neu laden
        </Button>
      )}
      {resetSegmentMutation.error && (
        <ErrorState
          message={
            resetSegmentMutation.error instanceof ApiError
              ? resetSegmentMutation.error.message
              : "Zurücksetzen fehlgeschlagen."
          }
        />
      )}
      {resetAllMutation.error && (
        <ErrorState
          message={
            resetAllMutation.error instanceof ApiError
              ? resetAllMutation.error.message
              : "Zurücksetzen fehlgeschlagen."
          }
        />
      )}

      {/* Segment list */}
      <div className="transcript-editor__segments">
        {viewQuery.isLoading && <LoadingState message="Lade Transkript …" />}
        {viewQuery.error && (
          <ErrorState
            message={
              viewQuery.error instanceof ApiError
                ? viewQuery.error.message
                : "Transkript konnte nicht geladen werden."
            }
          />
        )}
        {transcript && transcript.segments.length === 0 && (
          <EmptyState title="Keine Segmente" description="Dieses Transkript enthält keine Segmente." />
        )}
        {transcript &&
          transcript.segments.map((seg) => (
            <SegmentRow
              key={seg.id}
              segment={seg}
              draft={drafts[seg.id]}
              dirty={drafts[seg.id] ? isDraftDirty(drafts[seg.id]) : false}
              onDraftChange={(v) => setDraft(seg.id, v)}
              onClearDraft={() => clearDraft(seg.id)}
              onSeek={() => seekTo(seg.start)}
              onResetSegment={() => doResetSegment(seg.id)}
              resetting={resetSegmentMutation.isPending}
            />
          ))}
      </div>

      <ConfirmDialog
        open={confirmResetAll}
        onOpenChange={(open) => {
          if (!open) setConfirmResetAll(false);
        }}
        title="Alle Korrekturen zurücksetzen"
        description="Möchtest du wirklich alle gespeicherten Korrekturen entfernen? Die Raw-Texte bleiben erhalten; die Revision wird erhöht."
        confirmLabel="Zurücksetzen"
        cancelLabel="Abbrechen"
        onConfirm={doResetAll}
        onCancel={() => setConfirmResetAll(false)}
      />

      <ConfirmDialog
        open={confirmDiscard !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDiscard(null);
        }}
        title="Ungespeicherte Änderungen verwerfen"
        description="Du hast ungespeicherte Korrekturen. Wirklich verwerfen?"
        confirmLabel="Verwerfen"
        cancelLabel="Behalten"
        onConfirm={() => {
          const fn = confirmDiscard;
          setConfirmDiscard(null);
          if (fn) fn();
        }}
        onCancel={() => setConfirmDiscard(null)}
      />
    </div>
  );
}

interface SegmentRowProps {
  segment: TranscriptSegment;
  draft: DraftSegment | undefined;
  dirty: boolean;
  onDraftChange: (value: string) => void;
  onClearDraft: () => void;
  onSeek: () => void;
  onResetSegment: () => void;
  resetting: boolean;
}

function SegmentRow({
  segment,
  draft,
  dirty,
  onDraftChange,
  onClearDraft,
  onSeek,
  onResetSegment,
  resetting,
}: SegmentRowProps) {
  const serverCorrected = segment.corrected_text ?? null;
  const draftValue = draft?.draft;
  // The textarea shows the draft if present, else the server value, else
  // empty string (which means "no correction").
  const textValue =
    draftValue !== undefined
      ? draftValue ?? ""
      : serverCorrected ?? "";

  const hasServerCorrection = serverCorrected !== null;

  return (
    <div className={`transcript-editor__segment${dirty ? " is-dirty" : ""}${hasServerCorrection ? " is-corrected" : ""}`}>
      <div className="transcript-editor__segment-header">
        <button
          type="button"
          className="transcript-editor__segment-time"
          onClick={onSeek}
          title="Zum Segmentbeginn springen"
        >
          {formatTimestamp(segment.start)} – {formatTimestamp(segment.end)}
        </button>
        <div className="transcript-editor__segment-badges">
          {dirty && (
            <Badge variant="warning" title="Ungespeicherte Änderung">
              <Check size={10} /> geändert
            </Badge>
          )}
          {hasServerCorrection && !dirty && (
            <Badge variant="success" title="Korrigiert">
              korrigiert
            </Badge>
          )}
        </div>
        <div className="transcript-editor__segment-actions">
          {dirty && (
            <Button variant="secondary" size="sm" onClick={onClearDraft} title="Lokale Änderung verwerfen">
              <X size={12} />
            </Button>
          )}
          {hasServerCorrection && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onResetSegment}
              disabled={resetting}
              title="Gespeicherte Korrektur zurücksetzen"
            >
              <RotateCcw size={12} />
            </Button>
          )}
        </div>
      </div>
      <div className="transcript-editor__segment-raw" title="Original ASR-Text (nicht editierbar)">
        <span className="transcript-editor__segment-raw-label">Raw:</span>
        <span className="transcript-editor__segment-raw-text">{segment.raw_text}</span>
      </div>
      <label className="transcript-editor__segment-correction">
        <span className="transcript-editor__segment-correction-label">Korrektur:</span>
        <textarea
          className="transcript-editor__segment-textarea"
          value={textValue}
          onChange={(e) => onDraftChange(e.target.value)}
          placeholder="Korrektur eingeben …"
          rows={2}
        />
      </label>
    </div>
  );
}

/** Small standalone icon used in the empty preview hint. */
export function TranscriptEditorPlaceholderIcon() {
  return <ClipboardList size={16} />;
}
