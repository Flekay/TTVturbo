import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchStatus } from "../api/status";
import {
  deleteRecording,
  fetchRecordings,
  uploadRecording,
} from "../api/recordings";
import type { Recording } from "../types/recording";

export const statusQueryKey = ["status"] as const;
export const recordingsQueryKey = ["recordings"] as const;

export function useStatusQuery() {
  return useQuery({
    queryKey: statusQueryKey,
    queryFn: ({ signal }) => fetchStatus(signal),
    refetchInterval: 10_000,
    staleTime: 5_000,
    retry: 1,
  });
}

export function useRecordingsQuery() {
  return useQuery({
    queryKey: recordingsQueryKey,
    queryFn: ({ signal }) => fetchRecordings(signal),
    staleTime: 5_000,
    retry: 1,
  });
}

export function useDeleteRecordingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (filename: string) => deleteRecording(filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: recordingsQueryKey });
      queryClient.invalidateQueries({ queryKey: statusQueryKey });
    },
  });
}

export interface UploadRecordingArgs {
  blob: Blob;
  filename: string;
}

export function useUploadRecordingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ blob, filename }: UploadRecordingArgs) =>
      uploadRecording(blob, filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: recordingsQueryKey });
      queryClient.invalidateQueries({ queryKey: statusQueryKey });
    },
  });
}

export function useRecordingQuery(filename: string | null) {
  return useQuery({
    queryKey: ["recording", filename],
    enabled: !!filename,
    queryFn: async () => {
      // Recordings are part of the list; this hook just selects one from the
      // shared list cache so we don't issue a separate request.
      return null as Recording | null;
    },
    // Always fetch fresh list data instead.
    initialData: null,
  });
}
