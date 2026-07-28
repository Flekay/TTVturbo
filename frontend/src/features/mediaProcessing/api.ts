import { apiClient } from "../../api/client";
import {
  transcriptionRuntimeStatusSchema,
  mediaJobSchema,
  transcriptionListResponseSchema,
  audioArtifactSchema,
  pipelineRunSchema,
  pipelineRunListResponseSchema,
  pipelineRunDeleteResponseSchema,
  vodTranscriptionsResponseSchema,
  transcriptViewSchema,
  transcriptRevisionsResponseSchema,
  conversationMiningRuntimeStatusSchema,
  miningRunSchema,
  miningRunListResponseSchema,
  miningRunDeleteResponseSchema,
} from "./schemas";
import type {
  StartTranscriptionRequest,
  StartPipelineRunRequest,
  StartPipelineRunFromUrlRequest,
  StartAudioExtractionRequest,
  TranscriptionRuntimeStatus,
  MediaJob,
  TranscriptionListResponse,
  AudioArtifact,
  PipelineRun,
  PipelineRunListResponse,
  PipelineRunDeleteResponse,
  VodTranscriptionsResponse,
  TranscriptView,
  TranscriptRevisionsResponse,
  SaveCorrectionsRequest,
  ConversationMiningRuntimeStatus,
  MiningRun,
  MiningRunListResponse,
  MiningRunDeleteResponse,
  StartMiningRunRequest,
} from "./types";

const TRANSCRIPTIONS = "/api/transcriptions";
const PIPELINE_RUNS = "/api/pipeline-runs";
const VODS = "/api/vods";
const MINING = "/api/conversation-mining";

// ---------------------------------------------------------------------------
// Transcription
// ---------------------------------------------------------------------------

export function fetchTranscriptionRuntimeStatus(
  signal?: AbortSignal,
): Promise<TranscriptionRuntimeStatus> {
  return apiClient.get("/api/transcription/status", {
    schema: transcriptionRuntimeStatusSchema,
    signal,
  });
}

export function preloadTranscriptionModel(): Promise<{
  ok: boolean;
  model: string;
  repo_id: string;
}> {
  return apiClient.post("/api/transcription/preload-model", {
    schema: z.object({
      ok: z.boolean(),
      model: z.string(),
      repo_id: z.string(),
    }),
    timeoutMs: 600_000,
  });
}

export function startTranscription(request: StartTranscriptionRequest): Promise<MediaJob> {
  return apiClient.post(TRANSCRIPTIONS, { body: request, schema: mediaJobSchema });
}

export function uploadAndTranscribe(
  file: File,
  language?: string,
  model?: string,
  modelFamily?: string,
  hotwords?: string,
): Promise<MediaJob> {
  const formData = new FormData();
  formData.append("file", file);
  if (language) formData.append("language", language);
  if (model) formData.append("model", model);
  if (modelFamily) formData.append("model_family", modelFamily);
  if (hotwords) formData.append("hotwords", hotwords);
  return apiClient.post(`${TRANSCRIPTIONS}/upload`, {
    body: formData,
    schema: mediaJobSchema,
    timeoutMs: 600_000,
  });
}

export function fetchTranscriptions(
  sourceId?: string,
  signal?: AbortSignal,
): Promise<TranscriptionListResponse> {
  const qs = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
  return apiClient.get(`${TRANSCRIPTIONS}${qs}`, {
    schema: transcriptionListResponseSchema,
    signal,
  });
}

export function fetchTranscription(transcriptionId: string, signal?: AbortSignal): Promise<MediaJob> {
  return apiClient.get(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}`, {
    schema: mediaJobSchema,
    signal,
  });
}

export function cancelTranscription(transcriptionId: string): Promise<MediaJob> {
  return apiClient.post(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/cancel`, {
    schema: mediaJobSchema,
  });
}

export function retryTranscription(transcriptionId: string): Promise<MediaJob> {
  return apiClient.post(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/retry`, {
    schema: mediaJobSchema,
  });
}

export function deleteTranscription(
  transcriptionId: string,
): Promise<{ id: string; deleted: boolean }> {
  return apiClient.delete(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}`, {
    schema: z.object({ id: z.string(), deleted: z.boolean() }),
  });
}

export function fetchVodTranscriptions(
  vodId: string,
  signal?: AbortSignal,
): Promise<VodTranscriptionsResponse> {
  return apiClient.get(`${VODS}/${encodeURIComponent(vodId)}/transcriptions`, {
    schema: vodTranscriptionsResponseSchema,
    signal,
  });
}

/** Fetch transcriptions for any source (twitch_vod or file_upload). */
export function fetchSourceTranscriptions(
  sourceType: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<VodTranscriptionsResponse> {
  return apiClient.get(`/api/sources/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}/transcriptions`, {
    schema: vodTranscriptionsResponseSchema,
    signal,
  });
}

/** Build the transcript file download URL. */
export function transcriptFileUrl(transcriptionId: string, ext: "json" | "txt" | "srt" | "vtt"): string {
  return `${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/${ext}`;
}

// ---------------------------------------------------------------------------
// Editable transcript (corrections)
// ---------------------------------------------------------------------------

export function fetchTranscriptView(transcriptionId: string, signal?: AbortSignal): Promise<TranscriptView> {
  return apiClient.get(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/transcript`, {
    schema: transcriptViewSchema,
    signal,
  });
}

export function saveTranscriptCorrections(
  transcriptionId: string,
  request: SaveCorrectionsRequest,
): Promise<TranscriptView> {
  return apiClient.patch(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/corrections`, {
    body: request,
    schema: transcriptViewSchema,
  });
}

export function resetSegmentCorrection(
  transcriptionId: string,
  segmentId: string,
): Promise<TranscriptView> {
  return apiClient.post(
    `${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/segments/${encodeURIComponent(segmentId)}/reset`,
    { schema: transcriptViewSchema },
  );
}

export function resetAllCorrections(transcriptionId: string): Promise<TranscriptView> {
  return apiClient.post(
    `${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/reset-corrections`,
    { schema: transcriptViewSchema },
  );
}

export function fetchTranscriptRevisions(
  transcriptionId: string,
  signal?: AbortSignal,
): Promise<TranscriptRevisionsResponse> {
  return apiClient.get(`${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/revisions`, {
    schema: transcriptRevisionsResponseSchema,
    signal,
  });
}

// ---------------------------------------------------------------------------
// Audio artifacts
// ---------------------------------------------------------------------------

export function fetchAudioArtifact(vodId: string, signal?: AbortSignal): Promise<AudioArtifact> {
  return apiClient.get(`${VODS}/${encodeURIComponent(vodId)}/artifacts/audio`, {
    schema: audioArtifactSchema,
    signal,
  });
}

export function startAudioExtraction(
  vodId: string,
  request: StartAudioExtractionRequest = {},
): Promise<MediaJob | AudioArtifact> {
  return apiClient.post(`${VODS}/${encodeURIComponent(vodId)}/artifacts/audio`, {
    body: request,
    schema: audioArtifactSchema.or(mediaJobSchema),
  });
}

/** Build the audio file download URL. */
export function audioFileUrl(vodId: string): string {
  return `${VODS}/${encodeURIComponent(vodId)}/artifacts/audio/file`;
}

/** Build the audio file download URL for any source type. */
export function sourceAudioFileUrl(sourceType: string, sourceId: string): string {
  return `/api/sources/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}/artifacts/audio/file`;
}

/** Fetch audio artifact metadata for any source type. */
export function fetchSourceAudioArtifact(
  sourceType: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<AudioArtifact> {
  return apiClient.get(`/api/sources/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}/artifacts/audio`, {
    schema: audioArtifactSchema,
    signal,
  });
}

/** Start audio extraction for any source type. */
export function startSourceAudioExtraction(
  sourceType: string,
  sourceId: string,
  request: StartAudioExtractionRequest = {},
): Promise<MediaJob | AudioArtifact> {
  return apiClient.post(`/api/sources/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}/artifacts/audio`, {
    body: request,
    schema: audioArtifactSchema.or(mediaJobSchema),
  });
}

// ---------------------------------------------------------------------------
// Pipeline runs
// ---------------------------------------------------------------------------

export function startPipelineRun(request: StartPipelineRunRequest): Promise<PipelineRun> {
  return apiClient.post(PIPELINE_RUNS, { body: request, schema: pipelineRunSchema });
}

/** Start a VOD pipeline run from a Twitch VOD or clip URL (primary entry point). */
export function startVodPipelineRun(request: StartPipelineRunFromUrlRequest): Promise<PipelineRun> {
  return apiClient.post("/api/vod-pipeline/runs", { body: request, schema: pipelineRunSchema });
}

export interface PipelineRunsFilter {
  sourceId?: string;
  status?: string;
  profileId?: string;
  sourceType?: string;
  search?: string;
  limit?: number;
}

export function fetchPipelineRuns(
  sourceId?: string,
  signal?: AbortSignal,
): Promise<PipelineRunListResponse> {
  const qs = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
  return apiClient.get(`${PIPELINE_RUNS}${qs}`, {
    schema: pipelineRunListResponseSchema,
    signal,
  });
}

/** Fetch pipeline runs with the full filter set (status, profile, search, ...). */
export function fetchPipelineRunsFiltered(
  filter: PipelineRunsFilter,
  signal?: AbortSignal,
): Promise<PipelineRunListResponse> {
  const params = new URLSearchParams();
  if (filter.sourceId) params.set("source_id", filter.sourceId);
  if (filter.status) params.set("status", filter.status);
  if (filter.profileId) params.set("profile_id", filter.profileId);
  if (filter.sourceType) params.set("source_type", filter.sourceType);
  if (filter.search) params.set("search", filter.search);
  if (filter.limit !== undefined && filter.limit > 0) params.set("limit", String(filter.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiClient.get(`${PIPELINE_RUNS}${qs}`, {
    schema: pipelineRunListResponseSchema,
    signal,
  });
}

export function fetchPipelineRun(runId: string, signal?: AbortSignal): Promise<PipelineRun> {
  return apiClient.get(`${PIPELINE_RUNS}/${encodeURIComponent(runId)}`, {
    schema: pipelineRunSchema,
    signal,
  });
}

export function cancelPipelineRun(runId: string): Promise<PipelineRun> {
  return apiClient.post(`${PIPELINE_RUNS}/${encodeURIComponent(runId)}/cancel`, {
    schema: pipelineRunSchema,
  });
}

export function retryPipelineRun(runId: string): Promise<PipelineRun> {
  return apiClient.post(`${PIPELINE_RUNS}/${encodeURIComponent(runId)}/retry`, {
    schema: pipelineRunSchema,
  });
}

export function deletePipelineRun(runId: string): Promise<PipelineRunDeleteResponse> {
  return apiClient.delete(`${PIPELINE_RUNS}/${encodeURIComponent(runId)}`, {
    schema: pipelineRunDeleteResponseSchema,
  });
}

export function fetchVodPipelineRuns(
  vodId: string,
  signal?: AbortSignal,
): Promise<PipelineRunListResponse> {
  return apiClient.get(`${VODS}/${encodeURIComponent(vodId)}/pipeline-runs`, {
    schema: pipelineRunListResponseSchema,
    signal,
  });
}

// Import zod for the union schema used in startAudioExtraction.
import { z } from "zod";

// ---------------------------------------------------------------------------
// Conversation Mining
// ---------------------------------------------------------------------------

export function fetchMiningRuntimeStatus(
  signal?: AbortSignal,
): Promise<ConversationMiningRuntimeStatus> {
  return apiClient.get(`${MINING}/status`, {
    schema: conversationMiningRuntimeStatusSchema,
    signal,
  });
}

export function startMiningRun(request: StartMiningRunRequest): Promise<MiningRun> {
  return apiClient.post(`${MINING}/runs`, { body: request, schema: miningRunSchema });
}

export function fetchMiningRuns(
  filter: { mediaItemId?: string; transcriptId?: string; status?: string; stale?: boolean } = {},
  signal?: AbortSignal,
): Promise<MiningRunListResponse> {
  const params = new URLSearchParams();
  if (filter.mediaItemId) params.set("media_item_id", filter.mediaItemId);
  if (filter.transcriptId) params.set("transcript_id", filter.transcriptId);
  if (filter.status) params.set("status", filter.status);
  if (filter.stale !== undefined) params.set("stale", String(filter.stale));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiClient.get(`${MINING}/runs${qs}`, {
    schema: miningRunListResponseSchema,
    signal,
  });
}

export function fetchMiningRun(runId: string, signal?: AbortSignal): Promise<MiningRun> {
  return apiClient.get(`${MINING}/runs/${encodeURIComponent(runId)}`, {
    schema: miningRunSchema,
    signal,
  });
}

export function cancelMiningRun(runId: string): Promise<MiningRun> {
  return apiClient.post(`${MINING}/runs/${encodeURIComponent(runId)}/cancel`, {
    schema: miningRunSchema,
  });
}

export function retryMiningRun(runId: string): Promise<MiningRun> {
  return apiClient.post(`${MINING}/runs/${encodeURIComponent(runId)}/retry`, {
    schema: miningRunSchema,
  });
}

export function deleteMiningRun(runId: string): Promise<MiningRunDeleteResponse> {
  return apiClient.delete(`${MINING}/runs/${encodeURIComponent(runId)}`, {
    schema: miningRunDeleteResponseSchema,
  });
}
