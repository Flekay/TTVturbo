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
} from "./schemas";
import type {
  StartTranscriptionRequest,
  StartPipelineRunRequest,
  StartAudioExtractionRequest,
  TranscriptionRuntimeStatus,
  MediaJob,
  TranscriptionListResponse,
  AudioArtifact,
  PipelineRun,
  PipelineRunListResponse,
  PipelineRunDeleteResponse,
  VodTranscriptionsResponse,
} from "./types";

const TRANSCRIPTIONS = "/api/transcriptions";
const PIPELINE_RUNS = "/api/pipeline-runs";
const VODS = "/api/vods";

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

/** Build the transcript file download URL. */
export function transcriptFileUrl(transcriptionId: string, ext: "json" | "txt" | "srt" | "vtt"): string {
  return `${TRANSCRIPTIONS}/${encodeURIComponent(transcriptionId)}/${ext}`;
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

// ---------------------------------------------------------------------------
// Pipeline runs
// ---------------------------------------------------------------------------

export function startPipelineRun(request: StartPipelineRunRequest): Promise<PipelineRun> {
  return apiClient.post(PIPELINE_RUNS, { body: request, schema: pipelineRunSchema });
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
