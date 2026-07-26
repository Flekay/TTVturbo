import { apiClient, apiRequest } from "../../api/client";
import {
  voiceScriptPackSchema,
  voiceProfileSchema,
  voiceProfileListResponseSchema,
  voiceProfileDeleteResponseSchema,
  voiceProfileReferenceSchema,
} from "./schemas";
import type {
  AttachReferenceRequest,
  CreateVoiceProfileRequest,
  PatchVoiceProfileRequest,
  VoiceProfile,
  VoiceProfileDeleteResponse,
  VoiceProfileListResponse,
  VoiceProfileReference,
  VoiceScriptPack,
} from "./types";

const BASE = "/api/voice-profiles";

export function fetchVoiceScripts(signal?: AbortSignal): Promise<VoiceScriptPack> {
  return apiClient.get(`${BASE}/scripts`, {
    schema: voiceScriptPackSchema,
    signal,
  });
}

export function fetchHoldoutScripts(signal?: AbortSignal): Promise<VoiceScriptPack> {
  return apiClient.get(`${BASE}/holdout-scripts`, {
    schema: voiceScriptPackSchema,
    signal,
  });
}

export function fetchVoiceProfiles(signal?: AbortSignal): Promise<VoiceProfileListResponse> {
  return apiClient.get(BASE, {
    schema: voiceProfileListResponseSchema,
    signal,
  });
}

export function createVoiceProfile(request: CreateVoiceProfileRequest): Promise<VoiceProfile> {
  return apiClient.post(BASE, {
    body: request,
    schema: voiceProfileSchema,
  });
}

export function fetchVoiceProfile(id: string, signal?: AbortSignal): Promise<VoiceProfile> {
  return apiClient.get(`${BASE}/${encodeURIComponent(id)}`, {
    schema: voiceProfileSchema,
    signal,
  });
}

export function patchVoiceProfile(
  id: string,
  request: PatchVoiceProfileRequest,
): Promise<VoiceProfile> {
  return apiRequest(`${BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: request,
    schema: voiceProfileSchema,
  });
}

export function deleteVoiceProfile(id: string): Promise<VoiceProfileDeleteResponse> {
  return apiClient.delete(`${BASE}/${encodeURIComponent(id)}`, {
    schema: voiceProfileDeleteResponseSchema,
  });
}

export function attachReference(
  profileId: string,
  scriptId: string,
  request: AttachReferenceRequest,
): Promise<VoiceProfileReference> {
  return apiRequest(
    `${BASE}/${encodeURIComponent(profileId)}/references/${encodeURIComponent(scriptId)}`,
    { method: "PUT", body: request, schema: voiceProfileReferenceSchema },
  );
}

export function detachReference(
  profileId: string,
  scriptId: string,
): Promise<VoiceProfileDeleteResponse> {
  return apiClient.delete(
    `${BASE}/${encodeURIComponent(profileId)}/references/${encodeURIComponent(scriptId)}`,
    { schema: voiceProfileDeleteResponseSchema },
  );
}

export function acceptReview(
  profileId: string,
  scriptId: string,
): Promise<VoiceProfileReference> {
  return apiClient.post(
    `${BASE}/${encodeURIComponent(profileId)}/references/${encodeURIComponent(scriptId)}/accept-review`,
    { schema: voiceProfileReferenceSchema },
  );
}
