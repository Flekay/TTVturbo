import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptReview,
  attachReference,
  createVoiceProfile,
  deleteVoiceProfile,
  fetchHoldoutScripts,
  fetchVoiceProfile,
  fetchVoiceProfiles,
  fetchVoiceScripts,
  patchVoiceProfile,
  detachReference,
} from "./api";
import type {
  AttachReferenceRequest,
  CreateVoiceProfileRequest,
  PatchVoiceProfileRequest,
} from "./types";

/**
 * TanStack Query integration for the Voice Profile feature.
 *
 * All query keys live under the `voice-profiles` namespace. Mutations only
 * invalidate the relevant slices — never the whole namespace — so concurrent
 * views do not refetch unnecessarily. Server state is never duplicated into
 * local React state; only ephemeral UI state (selection, filters, dialogs)
 * lives in useState.
 */

export const voiceProfilesQueryKey = ["voice-profiles"] as const;
export const voiceProfileQueryKey = (id: string) => ["voice-profiles", id] as const;
export const voiceProfileScriptsQueryKey = ["voice-profile-scripts"] as const;
export const voiceProfileHoldoutsQueryKey = ["voice-profile-holdouts"] as const;

export function useVoiceProfilesQuery() {
  return useQuery({
    queryKey: voiceProfilesQueryKey,
    queryFn: ({ signal }) => fetchVoiceProfiles(signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useVoiceProfileQuery(id: string | null) {
  return useQuery({
    queryKey: id ? voiceProfileQueryKey(id) : ["voice-profiles", "__none__"],
    enabled: !!id,
    queryFn: ({ signal }) => fetchVoiceProfile(id as string, signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useVoiceScriptsQuery() {
  return useQuery({
    queryKey: voiceProfileScriptsQueryKey,
    queryFn: ({ signal }) => fetchVoiceScripts(signal),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useHoldoutScriptsQuery() {
  return useQuery({
    queryKey: voiceProfileHoldoutsQueryKey,
    queryFn: ({ signal }) => fetchHoldoutScripts(signal),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useCreateVoiceProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateVoiceProfileRequest) => createVoiceProfile(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
    },
  });
}

export function usePatchVoiceProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, request }: { id: string; request: PatchVoiceProfileRequest }) =>
      patchVoiceProfile(id, request),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceProfileQueryKey(data.id) });
    },
  });
}

export function useDeleteVoiceProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteVoiceProfile(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
    },
  });
}

export function useAttachReferenceMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      scriptId,
      request,
    }: {
      profileId: string;
      scriptId: string;
      request: AttachReferenceRequest;
    }) => attachReference(profileId, scriptId, request),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceProfileQueryKey(profileId) });
    },
  });
}

export function useDetachReferenceMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, scriptId }: { profileId: string; scriptId: string }) =>
      detachReference(profileId, scriptId),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceProfileQueryKey(profileId) });
    },
  });
}

export function useAcceptReviewMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, scriptId }: { profileId: string; scriptId: string }) =>
      acceptReview(profileId, scriptId),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: voiceProfilesQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceProfileQueryKey(profileId) });
    },
  });
}
