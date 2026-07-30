import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchLibraryItems, uploadToLibrary, deleteLibraryItem } from "./api";
import type { FileType } from "./schemas";

export const libraryItemsQueryKey = (fileType?: FileType) =>
  ["library", "items", fileType ?? "all"] as const;

// Partial key used to invalidate every library-items query regardless of
// the active file_type filter.
const libraryItemsBaseKey = ["library", "items"] as const;

export function useLibraryItemsQuery(fileType?: FileType) {
  return useQuery({
    queryKey: libraryItemsQueryKey(fileType),
    queryFn: ({ signal }) => fetchLibraryItems(fileType, signal),
  });
}

export function useUploadToLibraryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadToLibrary(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryItemsBaseKey });
    },
  });
}

export function useDeleteLibraryItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => deleteLibraryItem(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryItemsBaseKey });
    },
  });
}
