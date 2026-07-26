import { apiClient } from "./client";
import {
  recordingDeleteResponseSchema,
  recordingListSchema,
  recordingUploadResponseSchema,
} from "../types/schemas";
import type {
  RecordingDeleteResponse,
  RecordingListResponse,
  RecordingUploadResponse,
} from "../types/recording";

export function fetchRecordings(signal?: AbortSignal): Promise<RecordingListResponse> {
  return apiClient.get("/api/recordings", { schema: recordingListSchema, signal });
}

export function deleteRecording(filename: string): Promise<RecordingDeleteResponse> {
  return apiClient.delete(`/api/recordings/${encodeURIComponent(filename)}`, {
    schema: recordingDeleteResponseSchema,
  });
}

export async function uploadRecording(
  blob: Blob,
  filename: string,
  signal?: AbortSignal,
): Promise<RecordingUploadResponse> {
  const formData = new FormData();
  formData.append("audio", blob, filename);
  return apiClient.post("/api/recordings", {
    body: formData,
    schema: recordingUploadResponseSchema,
    signal,
    timeoutMs: 120_000,
  });
}
