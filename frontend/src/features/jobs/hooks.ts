import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ACTIVE_JOB_STATUSES, cancelUnifiedJob, fetchAllJobs, retryUnifiedJob, type UnifiedJob } from "./api";

export function useAllJobs() {
  return useQuery({
    queryKey: ["jobs", "all"],
    queryFn: fetchAllJobs,
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      return jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status)) ? 1200 : 5000;
    },
  });
}

export function useCancelJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (job: UnifiedJob) => cancelUnifiedJob(job),
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (job: UnifiedJob) => retryUnifiedJob(job),
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  });
}
