import { apiClient } from "../../api/client";
import { uploadListResponseSchema } from "./schemas";
import type { UploadListResponse } from "./schemas";

const LIBRARY = "/api/library";

export function fetchUploads(signal?: AbortSignal): Promise<UploadListResponse> {
  return apiClient.get(`${LIBRARY}/uploads`, { schema: uploadListResponseSchema, signal });
}

export function uploadFileUrl(uploadId: string): string {
  return `${LIBRARY}/uploads/${encodeURIComponent(uploadId)}/file`;
}

export async function uploadToLibrary(file: File): Promise<unknown> {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.post(`${LIBRARY}/uploads`, { body: formData });
}

export async function deleteUpload(uploadId: string): Promise<unknown> {
  return apiClient.delete(`${LIBRARY}/uploads/${encodeURIComponent(uploadId)}`);
}
