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

/**
 * Known generation statuses. The schema accepts any string so the UI can
 * render a neutral badge for unknown future statuses instead of crashing.
 */
export const KNOWN_GENERATION_STATUSES = [
  "QUEUED",
  "VALIDATING_REFERENCE",
  "LOADING_MODEL",
  "GENERATING",
  "VALIDATING_OUTPUT",
  "READY",
  "FAILED",
] as const;

export type KnownGenerationStatus = (typeof KNOWN_GENERATION_STATUSES)[number];

export const generationStatusSchema = z.string();

export const voiceCloneStatusSchema = z.object({
  available: z.boolean(),
  busy: z.boolean(),
  active_generation_id: z.string().nullable(),
  model_id: z.string(),
  // Optional extended runtime fields. The backend may omit them until the
  // runtime integration is in place; the frontend must not assume they exist.
  device: z.string().nullable().optional(),
  device_name: z.string().nullable().optional(),
  torch_version: z.string().nullable().optional(),
  cuda_available: z.boolean().optional(),
  reasons: z.array(z.string()).optional(),
  warnings: z.array(z.string()).optional(),
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
  // Optional technical details, rendered only when the backend supplies them.
  // Field names must match voice_clone/schemas.py GenerationMetadata exactly.
  output_sha256: z.string().nullable().optional(),
  output_sample_rate: z.number().nullable().optional(),
  worker_exit_code: z.number().nullable().optional(),
  device_name: z.string().nullable().optional(),
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

export const qualityClassSchema = z.enum(["EXCELLENT", "GOOD", "REVIEW", "REJECT"]);

export const qualityMetricsSchema = z.object({
  technical: z.object({
    sample_rate: z.number(),
    channels: z.number(),
    frame_count: z.number(),
    duration_seconds: z.number(),
    subtype: z.string().nullable(),
    format: z.string().nullable(),
  }),
  levels: z.object({
    peak_dbfs: z.number().nullable(),
    rms_dbfs: z.number().nullable(),
    dc_offset: z.number(),
    clipping_sample_count: z.number(),
    clipping_sample_ratio: z.number(),
  }),
  silence: z.object({
    leading_silence_ms: z.number(),
    trailing_silence_ms: z.number(),
    total_silence_ratio: z.number(),
    voice_ratio: z.number(),
    frame_count_total: z.number(),
    frame_count_silent: z.number(),
    frame_count_active: z.number(),
  }),
  noise: z.object({
    estimated_noise_floor_dbfs: z.number().nullable(),
    estimated_snr_db: z.number().nullable(),
    active_frames_used: z.number(),
  }),
  dropouts: z.object({
    dropout_count: z.number(),
    dropout_total_ms: z.number(),
    longest_dropout_ms: z.number(),
  }),
  integrity: z.object({
    has_nan: z.boolean(),
    has_infinity: z.boolean(),
  }),
  quality: qualityClassSchema,
  reasons: z.array(z.string()),
  warnings: z.array(z.string()),
  voice_clone_reference: z.object({
    eligible: z.boolean(),
    quality: qualityClassSchema,
    reasons: z.array(z.string()),
    warnings: z.array(z.string()),
  }),
});

/** Best-effort error response schema. Many FastAPI errors return `{detail: ...}`. */
export const errorResponseSchema = z.object({
  detail: z.union([z.string(), z.array(z.any())]),
});
