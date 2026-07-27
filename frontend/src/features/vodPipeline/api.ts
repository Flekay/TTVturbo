import { apiClient } from "../../api/client";
import {
  twitchProfileSchema,
  twitchProfileListResponseSchema,
  twitchVodSchema,
  vodListResponseSchema,
  vodSyncResponseSchema,
  vodDeleteResponseSchema,
  twitchStatusSchema,
  vodLogResponseSchema,
} from "./schemas";
import type {
  CreateProfileRequest,
  ImportVodRequest,
  ListVodsParams,
  TwitchProfile,
  TwitchProfileListResponse,
  TwitchStatus,
  TwitchVod,
  VodDeleteResponse,
  VodListResponse,
  VodLogResponse,
  VodSyncResponse,
} from "./types";

const PROFILES = "/api/twitch/profiles";
const VODS = "/api/vods";

export function fetchTwitchStatus(signal?: AbortSignal): Promise<TwitchStatus> {
  return apiClient.get("/api/twitch/status", { schema: twitchStatusSchema, signal });
}

export function fetchTwitchProfiles(signal?: AbortSignal): Promise<TwitchProfileListResponse> {
  return apiClient.get(PROFILES, { schema: twitchProfileListResponseSchema, signal });
}

export function createTwitchProfile(request: CreateProfileRequest): Promise<TwitchProfile> {
  return apiClient.post(PROFILES, { body: request, schema: twitchProfileSchema });
}

export function fetchTwitchProfile(id: string, signal?: AbortSignal): Promise<TwitchProfile> {
  return apiClient.get(`${PROFILES}/${encodeURIComponent(id)}`, {
    schema: twitchProfileSchema,
    signal,
  });
}

export function refreshTwitchProfile(id: string): Promise<TwitchProfile> {
  return apiClient.post(`${PROFILES}/${encodeURIComponent(id)}/refresh`, {
    schema: twitchProfileSchema,
  });
}

export function deleteTwitchProfile(id: string): Promise<VodDeleteResponse> {
  return apiClient.delete(`${PROFILES}/${encodeURIComponent(id)}`, {
    schema: vodDeleteResponseSchema,
  });
}

export function syncVods(profileId: string): Promise<VodSyncResponse> {
  return apiClient.post(`${PROFILES}/${encodeURIComponent(profileId)}/sync-vods`, {
    schema: vodSyncResponseSchema,
  });
}

export function fetchVods(
  params: ListVodsParams = {},
  signal?: AbortSignal,
): Promise<VodListResponse> {
  const search = new URLSearchParams();
  if (params.profile_id) search.set("profile_id", params.profile_id);
  if (params.status) search.set("status", params.status);
  if (params.search) search.set("search", params.search);
  if (params.sort) search.set("sort", params.sort);
  const qs = search.toString();
  return apiClient.get(`${VODS}${qs ? `?${qs}` : ""}`, {
    schema: vodListResponseSchema,
    signal,
  });
}

export function fetchVod(id: string, signal?: AbortSignal): Promise<TwitchVod> {
  return apiClient.get(`${VODS}/${encodeURIComponent(id)}`, {
    schema: twitchVodSchema,
    signal,
  });
}

export function importVod(request: ImportVodRequest): Promise<TwitchVod> {
  return apiClient.post(`${VODS}/import`, { body: request, schema: twitchVodSchema });
}

export function startDownload(vodId: string): Promise<TwitchVod> {
  return apiClient.post(`${VODS}/${encodeURIComponent(vodId)}/download`, {
    schema: twitchVodSchema,
  });
}

export function cancelDownload(vodId: string): Promise<TwitchVod> {
  return apiClient.post(`${VODS}/${encodeURIComponent(vodId)}/cancel`, {
    schema: twitchVodSchema,
  });
}

export function retryDownload(vodId: string): Promise<TwitchVod> {
  return apiClient.post(`${VODS}/${encodeURIComponent(vodId)}/retry`, {
    schema: twitchVodSchema,
  });
}

export function deleteVod(vodId: string): Promise<VodDeleteResponse> {
  return apiClient.delete(`${VODS}/${encodeURIComponent(vodId)}`, {
    schema: vodDeleteResponseSchema,
  });
}

export function fetchVodLog(vodId: string, signal?: AbortSignal): Promise<VodLogResponse> {
  return apiClient.get(`${VODS}/${encodeURIComponent(vodId)}/log`, {
    schema: vodLogResponseSchema,
    signal,
  });
}

/** Build the file download URL for a READY VOD. */
export function vodFileUrl(vodId: string): string {
  return `${VODS}/${encodeURIComponent(vodId)}/file`;
}
