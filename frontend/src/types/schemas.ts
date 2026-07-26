import { z } from "zod";

export const backendStatusSchema = z.object({
  status: z.enum(["online", "offline"]),
  app_name: z.string(),
  version: z.string(),
  uptime_seconds: z.number(),
  recordings: z.object({
    count: z.number(),
    total_duration_seconds: z.number(),
    total_size_bytes: z.number(),
  }),
  storage: z.object({
    free_bytes: z.number(),
  }),
  features: z.object({
    recording: z.enum(["available", "unavailable", "not_implemented"]),
    voice_cloning: z.enum(["available", "unavailable", "not_implemented"]),
    vod_analysis: z.enum(["available", "unavailable", "not_implemented"]),
    video_editor: z.enum(["available", "unavailable", "not_implemented"]),
  }),
});

export const recordingSchema = z.object({
  filename: z.string(),
  created_at: z.string(),
  duration_seconds: z.number(),
  file_size_bytes: z.number(),
  audio_url: z.string(),
});

export const recordingListSchema = z.object({
  recordings: z.array(recordingSchema),
});

export const recordingUploadResponseSchema = z.object({
  filename: z.string(),
  url: z.string(),
  size_bytes: z.number(),
  probe: z.string().optional(),
});

export const recordingDeleteResponseSchema = z.object({
  filename: z.string(),
  deleted: z.boolean(),
});
