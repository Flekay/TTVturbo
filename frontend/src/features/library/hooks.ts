import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchLibraryItems, uploadToLibrary, uploadToLibraryTemporary, deleteLibraryItem } from "./api";
import type { FileType } from "./schemas";

export const libraryItemsQueryKey = (fileType?: FileType) =>
  ["library", "items", fileType ?? "all"] as const;

// Partial key used to invalidate every library-items query regardless of
// the active file_type filter.
const libraryItemsBaseKey = ["library", "items"] as const;

export function useLibraryItemsQuery(fileType?: FileType, options?: { includeTemporary?: boolean }) {
  return useQuery({
    queryKey: [...libraryItemsQueryKey(fileType), options?.includeTemporary ?? false],
    queryFn: ({ signal }) => fetchLibraryItems(fileType, signal, options),
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

export function useUploadToLibraryTemporaryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadToLibraryTemporary(file),
    onSuccess: () => {
      // Temporary items don't appear in the default list, but the editor
      // may need the updated cache to resolve the new item.
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
