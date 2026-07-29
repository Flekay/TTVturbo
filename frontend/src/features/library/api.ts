import { apiClient } from "../../api/client";
import { libraryItemListResponseSchema, libraryItemSchema } from "./schemas";
import type { LibraryItemListResponse, LibraryItem } from "./schemas";

const LIBRARY = "/api/library";

export function fetchLibraryItems(signal?: AbortSignal): Promise<LibraryItemListResponse> {
  return apiClient.get(`${LIBRARY}/items`, { schema: libraryItemListResponseSchema, signal });
}

export function fetchLibraryItem(itemId: string, signal?: AbortSignal): Promise<LibraryItem> {
  return apiClient.get(`${LIBRARY}/items/${encodeURIComponent(itemId)}`, {
    schema: libraryItemSchema,
    signal,
  });
}

export function libraryItemFileUrl(itemId: string): string {
  return `${LIBRARY}/items/${encodeURIComponent(itemId)}/file`;
}

export async function uploadToLibrary(file: File): Promise<LibraryItem> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("lifecycle", "PERSISTENT");
  return apiClient.post(`${LIBRARY}/uploads`, { body: formData, schema: libraryItemSchema });
}

export async function promoteLibraryItem(itemId: string): Promise<LibraryItem> {
  return apiClient.post(`${LIBRARY}/items/${encodeURIComponent(itemId)}/promote`, {
    schema: libraryItemSchema,
  });
}

export async function deleteLibraryItem(itemId: string): Promise<unknown> {
  return apiClient.delete(`${LIBRARY}/items/${encodeURIComponent(itemId)}`);
}
