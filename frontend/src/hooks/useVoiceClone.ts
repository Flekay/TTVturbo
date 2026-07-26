import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createGeneration,
  deleteGeneration,
  fetchGeneration,
  fetchGenerations,
  fetchReferenceQuality,
  fetchVoiceCloneStatus,
} from "../api/voiceClone";
import { KNOWN_GENERATION_STATUSES, type KnownGenerationStatus } from "../types/schemas";
import type { CreateGenerationRequest } from "../types/voiceClone";

export const voiceCloneStatusQueryKey = ["voice-clone-status"] as const;
export const generationsQueryKey = ["voice-clone-generations"] as const;

const ACTIVE_STATUSES: ReadonlySet<KnownGenerationStatus> = new Set<KnownGenerationStatus>([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
]);

function isActiveStatus(status: string | undefined): boolean {
  return !!status && ACTIVE_STATUSES.has(status as KnownGenerationStatus);
}

export function useReferenceQualityQuery(filename: string | null) {
  return useQuery({
    queryKey: ["voice-clone-reference-quality", filename],
    enabled: !!filename,
    queryFn: ({ signal }) => fetchReferenceQuality(filename as string, signal),
    staleTime: 30_000,
    retry: 0,
  });
}

/** Poll the voice-clone module status. While a generation is busy the
 * frontend refreshes this frequently so the user sees live phase changes.
 * When the tab is in the background, polling is paused to avoid unnecessary
 * network traffic and re-renders. */
export function useVoiceCloneStatusQuery() {
  return useQuery({
    queryKey: voiceCloneStatusQueryKey,
    queryFn: ({ signal }) => fetchVoiceCloneStatus(signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      // Only poll aggressively while a generation is active. When idle, stop
      // polling entirely; the query is refetched on demand (e.g. after a
      // mutation invalidates it).
      return data?.busy ? 2000 : false;
    },
    refetchIntervalInBackground: false,
    staleTime: 5_000,
    retry: 1,
  });
}

export function useGenerationsQuery() {
  return useQuery({
    queryKey: generationsQueryKey,
    queryFn: ({ signal }) => fetchGenerations(signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      const anyActive = data?.generations.some((g) => isActiveStatus(g.status));
      // Poll while at least one generation is active; stop once all are in a
      // terminal state (READY/FAILED/unknown). The list is still refetched on
      // mutations and manual refetches.
      return anyActive ? 2000 : false;
    },
    refetchIntervalInBackground: false,
    staleTime: 5_000,
    retry: 1,
  });
}

export function useGenerationQuery(id: string | null) {
  return useQuery({
    queryKey: ["voice-clone-generation", id],
    enabled: !!id,
    queryFn: ({ signal }) => fetchGeneration(id as string, signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      return isActiveStatus(data?.status) ? 2000 : false;
    },
    refetchIntervalInBackground: false,
    staleTime: 5_000,
    retry: 1,
  });
}

export function useCreateGenerationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateGenerationRequest) => createGeneration(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: generationsQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceCloneStatusQueryKey });
    },
  });
}

export function useDeleteGenerationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteGeneration(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: generationsQueryKey });
      queryClient.invalidateQueries({ queryKey: voiceCloneStatusQueryKey });
    },
  });
}

export { KNOWN_GENERATION_STATUSES };
