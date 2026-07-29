import { apiClient } from "../../api/client";

export type JobOperation =
  | "video_upscale"
  | "video_background_removal"
  | "video_text_edit"
  | "video_generation"
  | "rendering"
  | "transcription"
  | "pipeline"
  | "visual_analysis"
  | "conversation_mining"
  | "ideas_research";

export interface UnifiedJob {
  id: string;
  operation: JobOperation;
  label: string;
  status: string;
  progress: number | null;
  stage: string | null;
  createdAt: string | null;
  completedAt: string | null;
  sourceTitle: string | null;
  errorMessage: string | null;
  retryable: boolean;
  libraryItemId: string | null;
  actionId: string;
  raw: Record<string, unknown>;
}

interface JobSourceConfig {
  operation: JobOperation;
  label: string;
  path: string;
  responseKey: string;
  itemPath: string;
  cancelPath?: string;
  retryPath?: string;
  actionIdField?: string;
}

export const JOB_SOURCES: JobSourceConfig[] = [
  { operation: "video_upscale", label: "Video hochskalieren", path: "/api/video-upscale/jobs", responseKey: "jobs", itemPath: "/api/video-upscale/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "video_background_removal", label: "Hintergrund entfernen", path: "/api/video-background-removal/jobs", responseKey: "jobs", itemPath: "/api/video-background-removal/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "video_text_edit", label: "Video per Text bearbeiten", path: "/api/video-text-edit/jobs", responseKey: "jobs", itemPath: "/api/video-text-edit/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "video_generation", label: "Video generieren", path: "/api/video-generation/jobs", responseKey: "jobs", itemPath: "/api/video-generation/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "rendering", label: "Video rendern", path: "/api/rendering/jobs", responseKey: "jobs", itemPath: "/api/rendering/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "transcription", label: "Transkription", path: "/api/transcriptions", responseKey: "transcriptions", itemPath: "/api/transcriptions", cancelPath: "cancel", retryPath: "retry", actionIdField: "transcription_id" },
  { operation: "pipeline", label: "VOD-Verarbeitung", path: "/api/pipeline-runs", responseKey: "pipeline_runs", itemPath: "/api/pipeline-runs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "visual_analysis", label: "Visuelle Analyse", path: "/api/visual-analysis/jobs", responseKey: "jobs", itemPath: "/api/visual-analysis/jobs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "conversation_mining", label: "Conversation Mining", path: "/api/conversation-mining/runs", responseKey: "runs", itemPath: "/api/conversation-mining/runs", cancelPath: "cancel", retryPath: "retry" },
  { operation: "ideas_research", label: "Ideen-Recherche", path: "/api/ideas/research-runs", responseKey: "runs", itemPath: "/api/ideas/research-runs", cancelPath: "cancel", retryPath: "retry" },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function errorMessage(raw: Record<string, unknown>): string | null {
  const err = raw.error;
  if (typeof err === "string") return err;
  if (err && typeof err === "object") {
    const message = (err as Record<string, unknown>).message;
    return typeof message === "string" ? message : null;
  }
  return null;
}

function normalizeProgress(value: unknown): number | null {
  if (value && typeof value === "object") {
    return normalizeProgress((value as Record<string, unknown>).percent);
  }
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value >= 0 && value <= 1) return Math.round(value * 100);
  return Math.max(0, Math.min(100, value));
}

function normalizeJob(config: JobSourceConfig, value: unknown): UnifiedJob | null {
  const raw = asRecord(value);
  const id = typeof raw.id === "string" ? raw.id : null;
  if (!id) return null;
  const statusRaw = typeof raw.status === "string" ? raw.status : "UNKNOWN";
  const status = statusRaw.toUpperCase();
  const actionIdValue = config.actionIdField ? raw[config.actionIdField] : raw.id;
  const actionId = typeof actionIdValue === "string" ? actionIdValue : id;
  const progressRecord = asRecord(raw.progress);
  const sourceTitle =
    (typeof raw.source_title === "string" && raw.source_title) ||
    (typeof raw.title === "string" && raw.title) ||
    (typeof raw.prompt === "string" && raw.prompt.slice(0, 80)) ||
    (typeof raw.source_id === "string" && raw.source_id) ||
    null;
  return {
    id,
    operation: config.operation,
    label: config.label,
    status,
    progress: normalizeProgress(raw.progress ?? raw.percent),
    stage:
      (typeof raw.current_stage === "string" && raw.current_stage) ||
      (typeof raw.stage === "string" && raw.stage) ||
      (typeof progressRecord.phase === "string" && progressRecord.phase) ||
      null,
    createdAt:
      (typeof raw.created_at === "string" && raw.created_at) ||
      (typeof raw.started_at === "string" && raw.started_at) ||
      null,
    completedAt: typeof raw.completed_at === "string" ? raw.completed_at : null,
    sourceTitle,
    errorMessage: errorMessage(raw),
    retryable:
      Boolean(asRecord(raw.error).retryable) || ["FAILED", "CANCELED", "CANCELLED"].includes(status),
    libraryItemId: typeof raw.library_item_id === "string" ? raw.library_item_id : null,
    actionId,
    raw,
  };
}

export async function fetchAllJobs(): Promise<{ jobs: UnifiedJob[]; unavailable: JobOperation[] }> {
  const results = await Promise.allSettled(
    JOB_SOURCES.map(async (config) => {
      const response = await apiClient.get<Record<string, unknown>>(config.path, { timeoutMs: 10_000 });
      const list = response[config.responseKey];
      const values = Array.isArray(list) ? list : [];
      return values.map((entry) => normalizeJob(config, entry)).filter((entry): entry is UnifiedJob => entry !== null);
    }),
  );

  const jobs: UnifiedJob[] = [];
  const unavailable: JobOperation[] = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") jobs.push(...result.value);
    else unavailable.push(JOB_SOURCES[index].operation);
  });
  jobs.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
  return { jobs, unavailable };
}

export async function cancelUnifiedJob(job: UnifiedJob): Promise<void> {
  const config = JOB_SOURCES.find((source) => source.operation === job.operation);
  if (!config?.cancelPath) throw new Error("Dieser Vorgang kann nicht abgebrochen werden.");
  await apiClient.post(`${config.itemPath}/${encodeURIComponent(job.actionId)}/${config.cancelPath}`);
}

export async function retryUnifiedJob(job: UnifiedJob): Promise<void> {
  const config = JOB_SOURCES.find((source) => source.operation === job.operation);
  if (!config?.retryPath) throw new Error("Dieser Vorgang kann nicht wiederholt werden.");
  await apiClient.post(`${config.itemPath}/${encodeURIComponent(job.actionId)}/${config.retryPath}`);
}

export const ACTIVE_JOB_STATUSES = new Set([
  "QUEUED",
  "RUNNING",
  "RETRYING",
  "CANCELING",
  "WAITING_FOR_GPU",
  "WAITING_FOR_DEPENDENCY",
  "EXPORTING",
]);
