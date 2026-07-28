import { z } from "zod";
import { apiClient } from "../../api/client";
import {
  asrPresetListResponseSchema,
  asrStatusSchema,
  asrBenchmarkListResponseSchema,
  asrBenchmarkSchema,
  asrRunDetailSchema,
  asrDefaultSelectionSchema,
  asrModelsResponseSchema,
  asrAudioDiagnosticListResponseSchema,
  asrAudioDiagnosticSchema,
} from "./schemas";
import type {
  CreateBenchmarkRequest,
  CreateAudioDiagnosticRequest,
  SelectDefaultRequest,
  AsrPresetListResponse,
  AsrStatus,
  AsrBenchmarkListResponse,
  AsrBenchmark,
  AsrRunDetail,
  AsrDefaultSelection,
  AsrModelsResponse,
  AsrAudioDiagnosticListResponse,
  AsrAudioDiagnostic,
} from "./types";

const ASR = "/api/asr";

export function fetchAsrPresets(signal?: AbortSignal): Promise<AsrPresetListResponse> {
  return apiClient.get(`${ASR}/presets`, { schema: asrPresetListResponseSchema, signal });
}

export function fetchAsrStatus(signal?: AbortSignal): Promise<AsrStatus> {
  return apiClient.get(`${ASR}/status`, { schema: asrStatusSchema, signal });
}

export function fetchAsrDefault(signal?: AbortSignal): Promise<AsrDefaultSelection> {
  return apiClient.get(`${ASR}/default`, { schema: asrDefaultSelectionSchema, signal });
}

export function setAsrDefault(request: SelectDefaultRequest): Promise<AsrDefaultSelection> {
  return apiClient.post(`${ASR}/default`, { body: request, schema: asrDefaultSelectionSchema });
}

export function fetchAsrModels(signal?: AbortSignal): Promise<AsrModelsResponse> {
  return apiClient.get(`${ASR}/models`, { schema: asrModelsResponseSchema, signal });
}

export function fetchAudioDiagnostics(
  sourceType: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<AsrAudioDiagnosticListResponse> {
  return apiClient.get(
    `${ASR}/audio-diagnostics/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}`,
    { schema: asrAudioDiagnosticListResponseSchema, signal },
  );
}

export function createAudioDiagnostic(
  request: CreateAudioDiagnosticRequest,
): Promise<AsrAudioDiagnostic> {
  return apiClient.post(`${ASR}/audio-diagnostics`, {
    body: request,
    schema: asrAudioDiagnosticSchema,
  });
}

export function audioArtifactUrl(diagnosticId: string, variant: string): string {
  return `${ASR}/audio-diagnostics/${encodeURIComponent(diagnosticId)}/artifacts/${encodeURIComponent(variant)}`;
}

export function createAsrBenchmark(request: CreateBenchmarkRequest): Promise<AsrBenchmark> {
  return apiClient.post(`${ASR}/benchmarks`, { body: request, schema: asrBenchmarkSchema });
}

export function fetchAsrBenchmarks(signal?: AbortSignal): Promise<AsrBenchmarkListResponse> {
  return apiClient.get(`${ASR}/benchmarks`, { schema: asrBenchmarkListResponseSchema, signal });
}

export function fetchAsrBenchmark(id: string, signal?: AbortSignal): Promise<AsrBenchmark> {
  return apiClient.get(`${ASR}/benchmarks/${encodeURIComponent(id)}`, {
    schema: asrBenchmarkSchema,
    signal,
  });
}

export function startAsrBenchmark(id: string): Promise<AsrBenchmark> {
  return apiClient.post(`${ASR}/benchmarks/${encodeURIComponent(id)}/start`, {
    schema: asrBenchmarkSchema,
  });
}

export function cancelAsrBenchmark(id: string): Promise<AsrBenchmark> {
  return apiClient.post(`${ASR}/benchmarks/${encodeURIComponent(id)}/cancel`, {
    schema: asrBenchmarkSchema,
  });
}

export function deleteAsrBenchmark(id: string): Promise<{ id: string; deleted: boolean }> {
  return apiClient.delete(`${ASR}/benchmarks/${encodeURIComponent(id)}`, {
    schema: z.object({ id: z.string(), deleted: z.boolean() }),
  });
}

export function selectDefaultFromBenchmark(
  benchmarkId: string,
  request: SelectDefaultRequest,
): Promise<AsrDefaultSelection> {
  return apiClient.post(`${ASR}/benchmarks/${encodeURIComponent(benchmarkId)}/select-default`, {
    body: request,
    schema: asrDefaultSelectionSchema,
  });
}

export function fetchAsrRun(
  benchmarkId: string,
  presetId: string,
  signal?: AbortSignal,
): Promise<AsrRunDetail> {
  return apiClient.get(
    `${ASR}/benchmarks/${encodeURIComponent(benchmarkId)}/runs/${encodeURIComponent(presetId)}`,
    { schema: asrRunDetailSchema, signal },
  );
}
