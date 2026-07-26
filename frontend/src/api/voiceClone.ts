import { apiClient } from "./client";
import {
  createGenerationResponseSchema,
  deleteGenerationResponseSchema,
  generationListSchema,
  generationMetadataSchema,
  qualityMetricsSchema,
  voiceCloneStatusSchema,
} from "../types/schemas";
import type {
  CreateGenerationRequest,
  CreateGenerationResponse,
  DeleteGenerationResponse,
  GenerationListResponse,
  GenerationMetadata,
  QualityMetrics,
  VoiceCloneStatusResponse,
} from "../types/voiceClone";

export function fetchVoiceCloneStatus(signal?: AbortSignal): Promise<VoiceCloneStatusResponse> {
  return apiClient.get("/api/voice-clone/status", {
    schema: voiceCloneStatusSchema,
    signal,
  });
}

export function fetchGenerations(signal?: AbortSignal): Promise<GenerationListResponse> {
  return apiClient.get("/api/voice-clone/generations", {
    schema: generationListSchema,
    signal,
  });
}

export function fetchGeneration(id: string, signal?: AbortSignal): Promise<GenerationMetadata> {
  return apiClient.get(`/api/voice-clone/generations/${encodeURIComponent(id)}`, {
    schema: generationMetadataSchema,
    signal,
  });
}

export function createGeneration(
  request: CreateGenerationRequest,
): Promise<CreateGenerationResponse> {
  return apiClient.post("/api/voice-clone/generations", {
    body: request,
    schema: createGenerationResponseSchema,
    timeoutMs: 60_000,
  });
}

export function deleteGeneration(id: string): Promise<DeleteGenerationResponse> {
  return apiClient.delete(`/api/voice-clone/generations/${encodeURIComponent(id)}`, {
    schema: deleteGenerationResponseSchema,
  });
}

export function generationAudioUrl(id: string): string {
  return `/api/voice-clone/generations/${encodeURIComponent(id)}/audio`;
}

export async function fetchReferenceQuality(
  filename: string,
  signal?: AbortSignal,
): Promise<QualityMetrics> {
  // The endpoint returns the full analysis result dict; we validate it with
  // the Zod schema so an invalid server response becomes a controlled ApiError
  // instead of an uncontrolled React crash.
  return apiClient.get(`/api/voice-clone/analyze-reference/${encodeURIComponent(filename)}`, {
    schema: qualityMetricsSchema,
    signal,
  });
}
