import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchLibraryItems, uploadToLibrary, deleteLibraryItem } from "./api";

const libraryItemsQueryKey = ["library", "items"] as const;

export function useLibraryItemsQuery() {
  return useQuery({
    queryKey: libraryItemsQueryKey,
    queryFn: ({ signal }) => fetchLibraryItems(signal),
  });
}

export function useUploadToLibraryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadToLibrary(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryItemsQueryKey });
    },
  });
}

export function useDeleteLibraryItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => deleteLibraryItem(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryItemsQueryKey });
    },
  });
}

// Legacy compatibility: keep the old hook names for any callers that
// still reference them.
export function useUploadsQuery() {
  return useLibraryItemsQuery();
}

export function useDeleteUploadMutation() {
  return useDeleteLibraryItemMutation();
}
