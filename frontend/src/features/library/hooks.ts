import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchUploads, uploadToLibrary, deleteUpload } from "./api";

const uploadsQueryKey = ["library", "uploads"] as const;

export function useUploadsQuery() {
  return useQuery({
    queryKey: uploadsQueryKey,
    queryFn: ({ signal }) => fetchUploads(signal),
  });
}

export function useUploadToLibraryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadToLibrary(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: uploadsQueryKey });
    },
  });
}

export function useDeleteUploadMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (uploadId: string) => deleteUpload(uploadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: uploadsQueryKey });
    },
  });
}
