import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createGeneration,
  deleteGeneration,
  fetchGeneration,
  fetchGenerations,
  fetchReferenceQuality,
  fetchVoiceCloneStatus,
} from "../api/voiceClone";
import type { CreateGenerationRequest } from "../types/voiceClone";

export const voiceCloneStatusQueryKey = ["voice-clone-status"] as const;
export const generationsQueryKey = ["voice-clone-generations"] as const;

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
 * frontend refreshes this frequently so the user sees live phase changes. */
export function useVoiceCloneStatusQuery() {
  return useQuery({
    queryKey: voiceCloneStatusQueryKey,
    queryFn: ({ signal }) => fetchVoiceCloneStatus(signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.busy ? 2000 : 15000;
    },
    staleTime: 1_000,
    retry: 1,
  });
}

export function useGenerationsQuery() {
  return useQuery({
    queryKey: generationsQueryKey,
    queryFn: ({ signal }) => fetchGenerations(signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      const anyActive = data?.generations.some((g) =>
        ["QUEUED", "VALIDATING_REFERENCE", "LOADING_MODEL", "GENERATING", "VALIDATING_OUTPUT"].includes(
          g.status,
        ),
      );
      return anyActive ? 2000 : 15000;
    },
    staleTime: 1_000,
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
      const active = data
        ? ["QUEUED", "VALIDATING_REFERENCE", "LOADING_MODEL", "GENERATING", "VALIDATING_OUTPUT"].includes(
            data.status,
          )
        : false;
      return active ? 2000 : false;
    },
    staleTime: 1_000,
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
