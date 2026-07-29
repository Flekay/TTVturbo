import { apiClient } from "../../api/client";
import type { LibraryItem } from "../library/schemas";

export type QuickToolId =
  | "video-upscale"
  | "video-background-removal"
  | "video-text-edit"
  | "video-generation";

export interface CapabilityJob {
  id: string;
  operation?: string;
  type?: string;
  status: string;
  progress?: number | null;
  current_stage?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  output_artifact_id?: string | null;
  output_artifact_ids?: string[];
  library_item_id?: string | null;
  library_item_ids?: string[];
  source_title?: string | null;
  prompt?: string;
  error?: { code?: string; message?: string; retryable?: boolean } | string | null;
  [key: string]: unknown;
}

export interface CapabilityStatus {
  available: boolean;
  configured?: boolean;
  busy?: boolean;
  reasons?: string[];
  error?: string | null;
  [key: string]: unknown;
}

const PREFIXES: Record<QuickToolId, string> = {
  "video-upscale": "/api/video-upscale",
  "video-background-removal": "/api/video-background-removal",
  "video-text-edit": "/api/video-text-edit",
  "video-generation": "/api/video-generation",
};

export async function fetchCapabilityStatus(tool: QuickToolId): Promise<CapabilityStatus> {
  return apiClient.get<CapabilityStatus>(`${PREFIXES[tool]}/status`);
}

export async function fetchCapabilityCapabilities(tool: QuickToolId): Promise<Record<string, unknown>> {
  return apiClient.get<Record<string, unknown>>(`${PREFIXES[tool]}/capabilities`);
}

export async function startCapabilityJob(
  tool: QuickToolId,
  payload: Record<string, unknown>,
): Promise<CapabilityJob> {
  return apiClient.post<CapabilityJob>(`${PREFIXES[tool]}/jobs`, {
    body: payload,
    timeoutMs: 60_000,
  });
}

export async function fetchCapabilityJob(tool: QuickToolId, jobId: string): Promise<CapabilityJob> {
  return apiClient.get<CapabilityJob>(`${PREFIXES[tool]}/jobs/${encodeURIComponent(jobId)}`);
}

export async function cancelCapabilityJob(tool: QuickToolId, jobId: string): Promise<CapabilityJob> {
  return apiClient.post<CapabilityJob>(`${PREFIXES[tool]}/jobs/${encodeURIComponent(jobId)}/cancel`);
}

export async function retryCapabilityJob(tool: QuickToolId, jobId: string): Promise<CapabilityJob> {
  return apiClient.post<CapabilityJob>(`${PREFIXES[tool]}/jobs/${encodeURIComponent(jobId)}/retry`);
}

export async function uploadTemporaryMedia(file: File): Promise<LibraryItem> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("lifecycle", "TEMPORARY");
  return apiClient.post<LibraryItem>("/api/library/uploads", {
    body: formData,
    timeoutMs: 5 * 60_000,
  });
}

export async function promoteLibraryItem(itemId: string): Promise<LibraryItem> {
  return apiClient.post<LibraryItem>(`/api/library/items/${encodeURIComponent(itemId)}/promote`);
}

export async function deleteTemporaryItem(itemId: string): Promise<void> {
  await apiClient.delete(`/api/library/items/${encodeURIComponent(itemId)}`);
}

export function libraryFileUrl(itemId: string): string {
  return `/api/library/items/${encodeURIComponent(itemId)}/file`;
}
