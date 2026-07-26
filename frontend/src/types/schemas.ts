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

export const generationStatusSchema = z.enum([
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
  "READY",
  "FAILED",
]);

export const voiceCloneStatusSchema = z.object({
  available: z.boolean(),
  busy: z.boolean(),
  active_generation_id: z.string().nullable(),
  model_id: z.string(),
});

export const generationMetadataSchema = z.object({
  id: z.string(),
  status: generationStatusSchema,
  reference_recording: z.string(),
  reference_sha256: z.string(),
  reference_text: z.string(),
  target_text: z.string(),
  language: z.string(),
  model_id: z.string(),
  model_revision: z.string(),
  created_at: z.string(),
  completed_at: z.string().nullable(),
  output_duration_seconds: z.number().nullable(),
  generation_seconds: z.number().nullable(),
  peak_vram_bytes: z.number().nullable(),
  quality: z.record(z.string(), z.unknown()),
  failure_reason: z.string().nullable(),
  warnings: z.array(z.string()),
});

export const generationListSchema = z.object({
  generations: z.array(generationMetadataSchema),
});

export const createGenerationResponseSchema = z.object({
  id: z.string(),
  status: generationStatusSchema,
});

export const deleteGenerationResponseSchema = z.object({
  id: z.string(),
  deleted: z.boolean(),
});
