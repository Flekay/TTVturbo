import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelDownload,
  createTwitchProfile,
  deleteTwitchProfile,
  deleteVod,
  fetchTwitchProfile,
  fetchTwitchProfiles,
  fetchTwitchStatus,
  fetchVod,
  fetchVodLog,
  fetchVods,
  importVod,
  refreshTwitchProfile,
  retryDownload,
  startDownload,
  syncVods,
} from "./api";
import type { CreateProfileRequest, ImportVodRequest, ListVodsParams } from "./types";

/**
 * TanStack Query integration for the VOD Pipeline feature.
 *
 * Query keys live under the `vod-pipeline` namespace. Mutations only
 * invalidate the relevant slices — never the whole namespace — so
 * concurrent views do not refetch unnecessarily. Active downloads are
 * polled on a short interval so progress updates without manual refresh.
 */

export const vodPipelineQueryKey = ["vod-pipeline"] as const;
export const twitchProfilesQueryKey = ["vod-pipeline", "profiles"] as const;
export const twitchProfileQueryKey = (id: string) => ["vod-pipeline", "profiles", id] as const;
export const twitchStatusQueryKey = ["vod-pipeline", "twitch-status"] as const;
export const vodsQueryKey = (params: ListVodsParams) => ["vod-pipeline", "vods", params] as const;
export const vodQueryKey = (id: string) => ["vod-pipeline", "vods", id] as const;
export const vodLogQueryKey = (id: string) => ["vod-pipeline", "vods", id, "log"] as const;

export function useTwitchStatusQuery() {
  return useQuery({
    queryKey: twitchStatusQueryKey,
    queryFn: ({ signal }) => fetchTwitchStatus(signal),
    staleTime: 5_000,
    refetchInterval: 30_000,
    retry: 1,
  });
}

export function useTwitchProfilesQuery() {
  return useQuery({
    queryKey: twitchProfilesQueryKey,
    queryFn: ({ signal }) => fetchTwitchProfiles(signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useTwitchProfileQuery(id: string | null) {
  return useQuery({
    queryKey: id ? twitchProfileQueryKey(id) : ["vod-pipeline", "profiles", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchTwitchProfile(id as string, signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useCreateTwitchProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateProfileRequest) => createTwitchProfile(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: twitchProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useRefreshTwitchProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => refreshTwitchProfile(profileId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: twitchProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: twitchProfileQueryKey(data.id) });
    },
  });
}

export function useDeleteTwitchProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => deleteTwitchProfile(profileId),
    onSettled: (_d, _e, profileId) => {
      queryClient.invalidateQueries({ queryKey: twitchProfilesQueryKey });
      queryClient.removeQueries({ queryKey: twitchProfileQueryKey(profileId) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useSyncVodsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => syncVods(profileId),
    onSuccess: (_d, profileId) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({ profile_id: profileId }) });
      queryClient.invalidateQueries({ queryKey: twitchProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: twitchProfileQueryKey(profileId) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useVodsQuery(params: ListVodsParams = {}, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: vodsQueryKey(params),
    queryFn: ({ signal }) => fetchVods(params, signal),
    staleTime: 3_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function useVodQuery(id: string | null, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: id ? vodQueryKey(id) : ["vod-pipeline", "vods", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchVod(id as string, signal),
    staleTime: 2_000,
    retry: 1,
    refetchInterval: options?.refetchInterval,
  });
}

export function useVodLogQuery(id: string | null) {
  return useQuery({
    queryKey: id ? vodLogQueryKey(id) : ["vod-pipeline", "vods", "__none__", "log"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchVodLog(id as string, signal),
    staleTime: 5_000,
    retry: 0,
  });
}

export function useImportVodMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ImportVodRequest) => importVod(request),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.invalidateQueries({
        queryKey: vodsQueryKey({ profile_id: data.profile_id }),
      });
      queryClient.invalidateQueries({ queryKey: twitchProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useStartDownloadMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vodId: string) => startDownload(vodId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.invalidateQueries({ queryKey: vodQueryKey(data.id) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useCancelDownloadMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vodId: string) => cancelDownload(vodId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.invalidateQueries({ queryKey: vodQueryKey(data.id) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useRetryDownloadMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vodId: string) => retryDownload(vodId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.invalidateQueries({ queryKey: vodQueryKey(data.id) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}

export function useDeleteVodMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vodId: string) => deleteVod(vodId),
    onSettled: (_d, _e, vodId) => {
      queryClient.invalidateQueries({ queryKey: vodsQueryKey({}) });
      queryClient.removeQueries({ queryKey: vodQueryKey(vodId) });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
}
