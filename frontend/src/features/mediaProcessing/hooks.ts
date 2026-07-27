import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchTranscriptionRuntimeStatus,
  preloadTranscriptionModel,
  startTranscription,
  fetchTranscriptions,
  fetchTranscription,
  cancelTranscription,
  retryTranscription,
  deleteTranscription,
  fetchVodTranscriptions,
  fetchAudioArtifact,
  startAudioExtraction,
  startPipelineRun,
  fetchPipelineRuns,
  fetchPipelineRun,
  cancelPipelineRun,
  retryPipelineRun,
  deletePipelineRun,
  fetchVodPipelineRuns,
} from "./api";
import type { StartTranscriptionRequest, StartPipelineRunRequest, StartAudioExtractionRequest } from "./types";

/**
 * TanStack Query integration for the Media Processing feature.
 *
 * Query keys live under the `media-processing` namespace. Mutations
 * invalidate the relevant slices. Active jobs are polled on a short
 * interval so progress updates without manual refresh.
 */

export const mediaProcessingQueryKey = ["media-processing"] as const;
export const transcriptionRuntimeQueryKey = ["media-processing", "transcription-runtime"] as const;
export const transcriptionsQueryKey = (sourceId?: string) =>
  ["media-processing", "transcriptions", sourceId ?? "all"] as const;
export const transcriptionQueryKey = (id: string) => ["media-processing", "transcriptions", id] as const;
export const vodTranscriptionsQueryKey = (vodId: string) =>
  ["media-processing", "vods", vodId, "transcriptions"] as const;
export const audioArtifactQueryKey = (vodId: string) =>
  ["media-processing", "vods", vodId, "audio-artifact"] as const;
export const pipelineRunsQueryKey = (sourceId?: string) =>
  ["media-processing", "pipeline-runs", sourceId ?? "all"] as const;
export const pipelineRunQueryKey = (id: string) => ["media-processing", "pipeline-runs", id] as const;
export const vodPipelineRunsQueryKey = (vodId: string) =>
  ["media-processing", "vods", vodId, "pipeline-runs"] as const;

// ---------------------------------------------------------------------------
// Transcription runtime status
// ---------------------------------------------------------------------------

export function useTranscriptionRuntimeQuery() {
  return useQuery({
    queryKey: transcriptionRuntimeQueryKey,
    queryFn: ({ signal }) => fetchTranscriptionRuntimeStatus(signal),
    staleTime: 5_000,
    refetchInterval: 15_000,
    retry: 1,
  });
}

export function usePreloadTranscriptionModelMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => preloadTranscriptionModel(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: transcriptionRuntimeQueryKey });
    },
  });
}

// ---------------------------------------------------------------------------
// Transcriptions
// ---------------------------------------------------------------------------

export function useTranscriptionsQuery(sourceId?: string, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: transcriptionsQueryKey(sourceId),
    queryFn: ({ signal }) => fetchTranscriptions(sourceId, signal),
    staleTime: 3_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function useTranscriptionQuery(id: string | null, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: id ? transcriptionQueryKey(id) : ["media-processing", "transcriptions", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchTranscription(id as string, signal),
    staleTime: 2_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function useVodTranscriptionsQuery(vodId: string | null) {
  return useQuery({
    queryKey: vodId ? vodTranscriptionsQueryKey(vodId) : ["media-processing", "vods", "__none__", "transcriptions"],
    enabled: !!vodId,
    queryFn: ({ signal }) => fetchVodTranscriptions(vodId as string, signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useStartTranscriptionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartTranscriptionRequest) => startTranscription(request),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: transcriptionsQueryKey() });
      queryClient.invalidateQueries({ queryKey: transcriptionsQueryKey(variables.source_id) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useCancelTranscriptionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (transcriptionId: string) => cancelTranscription(transcriptionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "transcriptions"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useRetryTranscriptionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (transcriptionId: string) => retryTranscription(transcriptionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "transcriptions"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useDeleteTranscriptionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (transcriptionId: string) => deleteTranscription(transcriptionId),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "transcriptions"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Audio artifacts
// ---------------------------------------------------------------------------

export function useAudioArtifactQuery(vodId: string | null) {
  return useQuery({
    queryKey: vodId ? audioArtifactQueryKey(vodId) : ["media-processing", "vods", "__none__", "audio-artifact"],
    enabled: !!vodId,
    queryFn: ({ signal }) => fetchAudioArtifact(vodId as string, signal),
    staleTime: 5_000,
    retry: 0,
  });
}

export function useStartAudioExtractionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ vodId, request }: { vodId: string; request?: StartAudioExtractionRequest }) =>
      startAudioExtraction(vodId, request ?? {}),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: audioArtifactQueryKey(variables.vodId) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Pipeline runs
// ---------------------------------------------------------------------------

export function usePipelineRunsQuery(sourceId?: string, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: pipelineRunsQueryKey(sourceId),
    queryFn: ({ signal }) => fetchPipelineRuns(sourceId, signal),
    staleTime: 3_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePipelineRunQuery(id: string | null, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: id ? pipelineRunQueryKey(id) : ["media-processing", "pipeline-runs", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchPipelineRun(id as string, signal),
    staleTime: 2_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function useVodPipelineRunsQuery(vodId: string | null) {
  return useQuery({
    queryKey: vodId ? vodPipelineRunsQueryKey(vodId) : ["media-processing", "vods", "__none__", "pipeline-runs"],
    enabled: !!vodId,
    queryFn: ({ signal }) => fetchVodPipelineRuns(vodId as string, signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useStartPipelineRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartPipelineRunRequest) => startPipelineRun(request),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: pipelineRunsQueryKey() });
      queryClient.invalidateQueries({ queryKey: pipelineRunsQueryKey(variables.source_id) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useCancelPipelineRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => cancelPipelineRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "pipeline-runs"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useRetryPipelineRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => retryPipelineRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "pipeline-runs"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useDeletePipelineRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => deletePipelineRun(runId),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "pipeline-runs"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}
