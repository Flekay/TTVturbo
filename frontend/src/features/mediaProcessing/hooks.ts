import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchTranscriptionRuntimeStatus,
  preloadTranscriptionModel,
  startTranscription,
  uploadAndTranscribe,
  fetchTranscriptions,
  fetchTranscription,
  cancelTranscription,
  retryTranscription,
  deleteTranscription,
  fetchVodTranscriptions,
  fetchSourceTranscriptions,
  fetchAudioArtifact,
  fetchSourceAudioArtifact,
  startAudioExtraction,
  startSourceAudioExtraction,
  startPipelineRun,
  startVodPipelineRun,
  fetchPipelineRuns,
  fetchPipelineRunsFiltered,
  fetchPipelineRun,
  cancelPipelineRun,
  retryPipelineRun,
  deletePipelineRun,
  fetchVodPipelineRuns,
  fetchTranscriptView,
  fetchTranscriptRevisions,
  saveTranscriptCorrections,
  resetSegmentCorrection,
  resetAllCorrections,
} from "./api";
import type {
  StartTranscriptionRequest,
  StartPipelineRunRequest,
  StartPipelineRunFromUrlRequest,
  StartAudioExtractionRequest,
  SaveCorrectionsRequest,
} from "./types";

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

export function useTranscriptionsQuery(
  sourceId?: string,
  options?: { refetchInterval?: number | false },
) {
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

export function useUploadTranscriptionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      language,
      model,
      model_family,
      hotwords,
    }: {
      file: File;
      language?: string;
      model?: string;
      model_family?: string;
      hotwords?: string;
    }) => uploadAndTranscribe(file, language, model, model_family, hotwords),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: transcriptionsQueryKey() });
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
// Editable transcript (corrections)
// ---------------------------------------------------------------------------

export const transcriptViewQueryKey = (id: string) =>
  ["media-processing", "transcript", id] as const;
export const transcriptRevisionsQueryKey = (id: string) =>
  ["media-processing", "transcript", id, "revisions"] as const;

export function useTranscriptViewQuery(id: string | null) {
  return useQuery({
    queryKey: id
      ? transcriptViewQueryKey(id)
      : ["media-processing", "transcript", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchTranscriptView(id as string, signal),
    staleTime: 0,
    retry: 0,
  });
}

export function useTranscriptRevisionsQuery(id: string | null) {
  return useQuery({
    queryKey: id
      ? transcriptRevisionsQueryKey(id)
      : ["media-processing", "transcript", "__none__", "revisions"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchTranscriptRevisions(id as string, signal),
    staleTime: 0,
    retry: 0,
  });
}

export function useSaveCorrectionsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      transcriptionId,
      request,
    }: {
      transcriptionId: string;
      request: SaveCorrectionsRequest;
    }) => saveTranscriptCorrections(transcriptionId, request),
    onSuccess: (_data, variables) => {
      queryClient.setQueryData(transcriptViewQueryKey(variables.transcriptionId), _data);
      queryClient.invalidateQueries({
        queryKey: transcriptRevisionsQueryKey(variables.transcriptionId),
      });
      queryClient.invalidateQueries({ queryKey: ["media-processing", "transcriptions"] });
    },
  });
}

export function useResetSegmentCorrectionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      transcriptionId,
      segmentId,
    }: {
      transcriptionId: string;
      segmentId: string;
    }) => resetSegmentCorrection(transcriptionId, segmentId),
    onSuccess: (data, variables) => {
      queryClient.setQueryData(transcriptViewQueryKey(variables.transcriptionId), data);
      queryClient.invalidateQueries({
        queryKey: transcriptRevisionsQueryKey(variables.transcriptionId),
      });
    },
  });
}

export function useResetAllCorrectionsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (transcriptionId: string) => resetAllCorrections(transcriptionId),
    onSuccess: (data, transcriptionId) => {
      queryClient.setQueryData(transcriptViewQueryKey(transcriptionId), data);
      queryClient.invalidateQueries({
        queryKey: transcriptRevisionsQueryKey(transcriptionId),
      });
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
// Audio artifacts / transcriptions for any source type (twitch_vod, file_upload)
// ---------------------------------------------------------------------------

export const sourceAudioArtifactQueryKey = (sourceType: string, sourceId: string) =>
  ["media-processing", "sources", sourceType, sourceId, "audio-artifact"] as const;
export const sourceTranscriptionsQueryKey = (sourceType: string, sourceId: string) =>
  ["media-processing", "sources", sourceType, sourceId, "transcriptions"] as const;

export function useSourceAudioArtifactQuery(sourceType: string | null, sourceId: string | null) {
  return useQuery({
    queryKey: sourceType && sourceId
      ? sourceAudioArtifactQueryKey(sourceType, sourceId)
      : ["media-processing", "sources", "__none__", "audio-artifact"],
    enabled: !!sourceType && !!sourceId,
    queryFn: ({ signal }) => fetchSourceAudioArtifact(sourceType as string, sourceId as string, signal),
    staleTime: 5_000,
    retry: 0,
  });
}

export function useStartSourceAudioExtractionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceType, sourceId, request }: { sourceType: string; sourceId: string; request?: StartAudioExtractionRequest }) =>
      startSourceAudioExtraction(sourceType, sourceId, request ?? {}),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: sourceAudioArtifactQueryKey(variables.sourceType, variables.sourceId) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useSourceTranscriptionsQuery(sourceType: string | null, sourceId: string | null, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: sourceType && sourceId
      ? sourceTranscriptionsQueryKey(sourceType, sourceId)
      : ["media-processing", "sources", "__none__", "transcriptions"],
    enabled: !!sourceType && !!sourceId,
    queryFn: ({ signal }) => fetchSourceTranscriptions(sourceType as string, sourceId as string, signal),
    staleTime: 3_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
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

/** Start a VOD pipeline run from a Twitch VOD or clip URL. */
export function useStartVodPipelineRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartPipelineRunFromUrlRequest) => startVodPipelineRun(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media-processing", "pipeline-runs"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export const pipelineRunsFilteredQueryKey = (filter: {
  status?: string;
  profileId?: string;
  sourceType?: string;
  search?: string;
  limit?: number;
}) =>
  [
    "media-processing",
    "pipeline-runs-filtered",
    filter.status ?? "all",
    filter.profileId ?? "all",
    filter.sourceType ?? "all",
    filter.search ?? "",
    filter.limit ?? 0,
  ] as const;

/** Fetch pipeline runs with the full filter set. */
export function usePipelineRunsFilteredQuery(
  filter: { status?: string; profileId?: string; sourceType?: string; search?: string; limit?: number },
  options?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: pipelineRunsFilteredQueryKey(filter),
    queryFn: ({ signal }) => fetchPipelineRunsFiltered(filter, signal),
    staleTime: 3_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
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
