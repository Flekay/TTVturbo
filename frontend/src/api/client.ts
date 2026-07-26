/**
 * Central typed fetch client for the TTVturbo backend.
 *
 * - normalises errors into ApiError
 * - supports request timeouts via AbortController
 * - validates JSON responses with the provided Zod schema
 * - no scattered raw fetch() calls elsewhere in the app
 */

import { ZodError, type ZodSchema } from "zod";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const DEFAULT_TIMEOUT_MS = 30_000;

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE" | "PUT" | "PATCH";
  signal?: AbortSignal;
  timeoutMs?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  body?: BodyInit | Record<string, any> | FormData;
  headers?: Record<string, string>;
  // When true, return raw Response (e.g. for file downloads).
  raw?: boolean;
  // Optional Zod schema to validate the parsed JSON body.
  schema?: ZodSchema<unknown>;
}

function isFormData(value: unknown): value is FormData {
  return typeof FormData !== "undefined" && value instanceof FormData;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions & { schema?: ZodSchema<T> },
): Promise<T> {
  const {
    method = "GET",
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    body,
    headers,
    raw = false,
    schema,
  } = options;

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  // Combine external signal with our timeout signal.
  if (signal) {
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  let response: Response;
  try {
    const init: RequestInit = {
      method,
      signal: controller.signal,
      headers: headers ?? {},
    };
    if (body !== undefined && !isFormData(body) && typeof body === "object") {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    } else if (body !== undefined) {
      init.body = body as BodyInit;
    }
    response = await fetch(path, init);
  } catch (err) {
    window.clearTimeout(timeoutId);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Request timed out", 408);
    }
    throw new ApiError(
      err instanceof Error ? err.message : "Network request failed",
      0,
    );
  }
  window.clearTimeout(timeoutId);

  if (raw) {
    if (!response.ok) {
      throw new ApiError(`Request failed: ${response.status}`, response.status);
    }
    return response as unknown as T;
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!response.ok) {
    const detail =
      typeof parsed === "object" && parsed && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : typeof parsed === "string"
          ? parsed
          : response.statusText;
    throw new ApiError(detail || `Request failed: ${response.status}`, response.status, parsed);
  }

  if (schema) {
    try {
      return schema.parse(parsed) as T;
    } catch (err) {
      if (err instanceof ZodError) {
        throw new ApiError("Invalid response shape from server", response.status, err.issues);
      }
      throw err;
    }
  }
  return parsed as T;
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, "method" | "body"> & { schema?: ZodSchema<T> }) =>
    apiRequest(path, { ...options, method: "GET" }),
  post: <T>(path: string, options?: Omit<RequestOptions, "method"> & { schema?: ZodSchema<T> }) =>
    apiRequest(path, { ...options, method: "POST" }),
  delete: <T>(path: string, options?: Omit<RequestOptions, "method" | "body"> & { schema?: ZodSchema<T> }) =>
    apiRequest(path, { ...options, method: "DELETE" }),
};
