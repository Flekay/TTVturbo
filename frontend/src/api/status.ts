import { apiClient } from "./client";
import { backendStatusSchema } from "../types/schemas";
import type { BackendStatus } from "../types/status";

export function fetchStatus(signal?: AbortSignal): Promise<BackendStatus> {
  return apiClient.get("/api/status", { schema: backendStatusSchema, signal });
}
