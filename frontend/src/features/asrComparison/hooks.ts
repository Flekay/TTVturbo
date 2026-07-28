import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAsrPresets,
  fetchAsrStatus,
  fetchAsrDefault,
  setAsrDefault,
  fetchAsrModels,
  fetchAudioDiagnostics,
  createAudioDiagnostic,
  createAsrBenchmark,
  fetchAsrBenchmarks,
  fetchAsrBenchmark,
  startAsrBenchmark,
  cancelAsrBenchmark,
  deleteAsrBenchmark,
  selectDefaultFromBenchmark,
  fetchAsrRun,
} from "./api";
import type { CreateBenchmarkRequest, CreateAudioDiagnosticRequest, SelectDefaultRequest } from "./types";

export const asrQueryKey = ["asr"] as const;
export const asrPresetsQueryKey = ["asr", "presets"] as const;
export const asrStatusQueryKey = ["asr", "status"] as const;
export const asrDefaultQueryKey = ["asr", "default"] as const;
export const asrModelsQueryKey = ["asr", "models"] as const;
export const asrAudioDiagnosticsQueryKey = (sourceType: string, sourceId: string) =>
  ["asr", "audio-diagnostics", sourceType, sourceId] as const;
export const asrBenchmarksQueryKey = ["asr", "benchmarks"] as const;
export const asrBenchmarkQueryKey = (id: string) => ["asr", "benchmarks", id] as const;
export const asrRunQueryKey = (benchmarkId: string, presetId: string) =>
  ["asr", "benchmarks", benchmarkId, "runs", presetId] as const;

export function useAsrPresetsQuery() {
  return useQuery({
    queryKey: asrPresetsQueryKey,
    queryFn: ({ signal }) => fetchAsrPresets(signal),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useAsrStatusQuery() {
  return useQuery({
    queryKey: asrStatusQueryKey,
    queryFn: ({ signal }) => fetchAsrStatus(signal),
    staleTime: 5_000,
    refetchInterval: 5_000,
    retry: 1,
  });
}

export function useAsrDefaultQuery() {
  return useQuery({
    queryKey: asrDefaultQueryKey,
    queryFn: ({ signal }) => fetchAsrDefault(signal),
    staleTime: 10_000,
    retry: 1,
  });
}

export function useSetAsrDefaultMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: SelectDefaultRequest) => setAsrDefault(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: asrDefaultQueryKey });
      queryClient.invalidateQueries({ queryKey: asrStatusQueryKey });
    },
  });
}

export function useCreateAsrBenchmarkMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateBenchmarkRequest) => createAsrBenchmark(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: asrBenchmarksQueryKey });
    },
  });
}

export function useAsrBenchmarksQuery() {
  return useQuery({
    queryKey: asrBenchmarksQueryKey,
    queryFn: ({ signal }) => fetchAsrBenchmarks(signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useAsrBenchmarkQuery(id: string | null, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: id ? asrBenchmarkQueryKey(id) : ["asr", "benchmarks", "__none__"],
    queryFn: ({ signal }) => fetchAsrBenchmark(id as string, signal),
    enabled: !!id,
    refetchInterval: options?.refetchInterval,
    retry: 1,
  });
}

export function useStartAsrBenchmarkMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => startAsrBenchmark(id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: asrBenchmarkQueryKey(data.id) });
      queryClient.invalidateQueries({ queryKey: asrBenchmarksQueryKey });
    },
  });
}

export function useCancelAsrBenchmarkMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => cancelAsrBenchmark(id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: asrBenchmarkQueryKey(data.id) });
    },
  });
}

export function useDeleteAsrBenchmarkMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAsrBenchmark(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: asrBenchmarksQueryKey });
    },
  });
}

export function useSelectDefaultFromBenchmarkMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ benchmarkId, presetId }: { benchmarkId: string; presetId: string }) =>
      selectDefaultFromBenchmark(benchmarkId, { preset_id: presetId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: asrDefaultQueryKey });
      queryClient.invalidateQueries({ queryKey: asrStatusQueryKey });
    },
  });
}

export function useAsrRunQuery(benchmarkId: string | null, presetId: string | null) {
  return useQuery({
    queryKey:
      benchmarkId && presetId
        ? asrRunQueryKey(benchmarkId, presetId)
        : ["asr", "runs", "__none__"],
    queryFn: ({ signal }) => fetchAsrRun(benchmarkId as string, presetId as string, signal),
    enabled: !!benchmarkId && !!presetId,
    retry: 1,
  });
}

export function useAsrModelsQuery() {
  return useQuery({
    queryKey: asrModelsQueryKey,
    queryFn: ({ signal }) => fetchAsrModels(signal),
    staleTime: 30_000,
    retry: 1,
  });
}

export function useAudioDiagnosticsQuery(sourceType: string | null, sourceId: string | null) {
  return useQuery({
    queryKey:
      sourceType && sourceId
        ? asrAudioDiagnosticsQueryKey(sourceType, sourceId)
        : ["asr", "audio-diagnostics", "__none__"],
    queryFn: ({ signal }) => fetchAudioDiagnostics(sourceType as string, sourceId as string, signal),
    enabled: !!sourceType && !!sourceId,
    staleTime: 10_000,
    retry: 1,
  });
}

export function useCreateAudioDiagnosticMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateAudioDiagnosticRequest) => createAudioDiagnostic(request),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: asrAudioDiagnosticsQueryKey(data.source_type, data.source_id),
      });
    },
  });
}
