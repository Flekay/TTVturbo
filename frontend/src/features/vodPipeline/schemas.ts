import { z } from "zod";

/**
 * VOD Pipeline feature Zod schemas.
 *
 * Mirror the actual backend responses from vod_pipeline_api.py. Unknown
 * additional fields are stripped by Zod; missing required fields and
 * unknown enum values surface as controlled ApiErrors instead of React
 * crashes. Status values are permissive strings so a future backend
 * status renders as a neutral badge instead of throwing.
 */

export const KNOWN_VOD_STATUSES = [
  "DISCOVERED",
  "QUEUED",
  "DOWNLOADING",
  "VERIFYING",
  "READY",
  "FAILED",
  "CANCELED",
] as const;

export type KnownVodStatus = (typeof KNOWN_VOD_STATUSES)[number];

/** Permissive status: known values get coloured badges, unknown ones neutral. */
export const vodStatusSchema = z.string();

export const vodProgressSchema = z.object({
  percent: z.number().nullable().optional(),
  downloaded_bytes: z.number().nullable().optional(),
  total_bytes: z.number().nullable().optional(),
  speed_bytes_per_second: z.number().nullable().optional(),
  eta_seconds: z.number().nullable().optional(),
});

export const vodDownloadSchema = z.object({
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  file_name: z.string().nullable().optional(),
  file_size_bytes: z.number().nullable().optional(),
  container: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  width: z.number().nullable().optional(),
  height: z.number().nullable().optional(),
  video_codec: z.string().nullable().optional(),
  audio_codec: z.string().nullable().optional(),
});

export const twitchProfileSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  login: z.string(),
  channel_url: z.string().optional(),
  display_name: z.string(),
  avatar_url: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  last_synced_at: z.string().nullable().optional(),
  // Attached by the API list endpoint; not persisted in the profile file.
  vod_count: z.number().optional(),
});

export const twitchProfileListResponseSchema = z.object({
  profiles: z.array(twitchProfileSchema),
});

export const twitchVodSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  profile_id: z.string().nullable().optional(),
  twitch_video_id: z.string(),
  library_item_id: z.string().nullable().optional(),
  source_url: z.string(),
  title: z.string(),
  description: z.string(),
  type: z.string(),
  language: z.string(),
  published_at: z.string().nullable().optional(),
  created_at: z.string(),
  duration_seconds: z.number().nullable().optional(),
  thumbnail_url: z.string(),
  view_count: z.number().nullable().optional(),
  status: vodStatusSchema,
  progress: vodProgressSchema,
  download: vodDownloadSchema,
  error: z.string().nullable().optional(),
  updated_at: z.string(),
});

export const vodListResponseSchema = z.object({
  vods: z.array(twitchVodSchema),
});

export const vodSyncResponseSchema = z.object({
  created: z.number(),
  updated: z.number(),
  unchanged: z.number(),
  total: z.number(),
});

export const vodDeleteResponseSchema = z.object({
  id: z.string(),
  deleted: z.boolean(),
});

export const twitchStatusSchema = z.object({
  available: z.boolean(),
  downloader_available: z.boolean().optional(),
  yt_dlp_version: z.string().nullable().optional(),
  ffprobe_available: z.boolean(),
  download_dir_writable: z.boolean(),
  reasons: z.array(z.string()),
  warnings: z.array(z.string()).optional(),
});

export const vodLogResponseSchema = z.object({
  id: z.string(),
  log: z.string(),
});

/** Best-effort error response schema. FastAPI errors return `{detail: ...}`. */
export const apiErrorSchema = z.object({
  detail: z.union([z.string(), z.array(z.any()), z.record(z.string(), z.any())]),
});
