import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelCapabilityJob,
  fetchCapabilityCapabilities,
  fetchCapabilityJob,
  fetchCapabilityStatus,
  promoteLibraryItem,
  retryCapabilityJob,
  startCapabilityJob,
  uploadTemporaryMedia,
  type CapabilityJob,
  type QuickToolId,
} from "./api";

const activeStatuses = new Set(["QUEUED", "RUNNING", "RETRYING", "CANCELING"]);

export function useCapabilityStatus(tool: QuickToolId, enabled = true) {
  return useQuery({
    queryKey: ["capability", tool, "status"],
    queryFn: () => fetchCapabilityStatus(tool),
    staleTime: 10_000,
    refetchInterval: enabled ? 15_000 : false,
    enabled,
  });
}

export function useCapabilityInfo(tool: QuickToolId) {
  return useQuery({
    queryKey: ["capability", tool, "capabilities"],
    queryFn: () => fetchCapabilityCapabilities(tool),
    staleTime: 30_000,
  });
}

export function useCapabilityJob(tool: QuickToolId, jobId: string | null) {
  return useQuery({
    queryKey: ["capability", tool, "job", jobId],
    queryFn: () => fetchCapabilityJob(tool, jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const job = query.state.data as CapabilityJob | undefined;
      return job && activeStatuses.has(job.status) ? 1000 : false;
    },
  });
}

export function useStartCapabilityJob(tool: QuickToolId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) => startCapabilityJob(tool, payload),
    onSuccess: (job) => {
      queryClient.setQueryData(["capability", tool, "job", job.id], job);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useUploadTemporaryMedia() {
  return useMutation({ mutationFn: uploadTemporaryMedia });
}

export function usePromoteItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: promoteLibraryItem,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["library", "items"] });
    },
  });
}

export function useCancelCapabilityJob(tool: QuickToolId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelCapabilityJob(tool, jobId),
    onSuccess: (job) => {
      queryClient.setQueryData(["capability", tool, "job", job.id], job);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useRetryCapabilityJob(tool: QuickToolId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => retryCapabilityJob(tool, jobId),
    onSuccess: (job) => {
      queryClient.setQueryData(["capability", tool, "job", job.id], job);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
