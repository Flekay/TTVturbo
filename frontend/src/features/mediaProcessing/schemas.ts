import { z } from "zod";

/**
 * Media Processing feature Zod schemas.
 *
 * Mirror the backend responses from media_processing_api.py. Unknown
 * additional fields are stripped by Zod; status values are permissive
 * strings so a future backend status renders as a neutral badge instead
 * of throwing.
 */

// ---------------------------------------------------------------------------
// Transcription runtime status
// ---------------------------------------------------------------------------

export const transcriptionRuntimeStatusSchema = z.object({
  available: z.boolean(),
  busy: z.boolean().optional(),
  busy_owner_type: z.string().nullable().optional(),
  model: z.string(),
  device: z.string(),
  compute_type: z.string(),
  device_name: z.string().nullable().optional(),
  model_cached: z.boolean().optional(),
  faster_whisper_importable: z.boolean().optional(),
  cuda_available: z.boolean().optional(),
  reasons: z.array(z.string()),
  warnings: z.array(z.string()).optional(),
});

// ---------------------------------------------------------------------------
// Media job (shared by audio extraction and transcription)
// ---------------------------------------------------------------------------

export const mediaProgressSchema = z.object({
  percent: z.number().nullable().optional(),
  processed_seconds: z.number().nullable().optional(),
  total_seconds: z.number().nullable().optional(),
  phase: z.string().nullable().optional(),
});

export const mediaJobSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  job_type: z.string(),
  source_type: z.string(),
  source_id: z.string(),
  status: z.string(),
  progress: mediaProgressSchema,
  options: z.record(z.string(), z.any()).optional(),
  result: z.any().nullable().optional(),
  error: z.string().nullable().optional(),
  depends_on: z.string().nullable().optional(),
  transcription_id: z.string().nullable().optional(),
  created_at: z.string(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  updated_at: z.string(),
  // Attached by the API when a transcript metadata record exists.
  transcript: z
    .object({
      schema_version: z.number().optional(),
      id: z.string(),
      source_type: z.string(),
      source_id: z.string(),
      audio_artifact: z.string(),
      model: z.string(),
      device: z.string(),
      compute_type: z.string(),
      language: z.string().nullable().optional(),
      language_probability: z.number().nullable().optional(),
      duration_seconds: z.number(),
      created_at: z.string(),
      status: z.string().optional(),
      segment_count: z.number().optional(),
      files: z
        .object({
          json: z.string().optional(),
          txt: z.string().optional(),
          srt: z.string().optional(),
          vtt: z.string().optional(),
        })
        .optional(),
    })
    .nullable()
    .optional(),
  transcript_status: z.string().nullable().optional(),
});

export const transcriptionListResponseSchema = z.object({
  transcriptions: z.array(mediaJobSchema),
});

// ---------------------------------------------------------------------------
// Audio artifact
// ---------------------------------------------------------------------------

export const audioArtifactSchema = z.object({
  schema_version: z.number().optional(),
  source_type: z.string(),
  source_id: z.string(),
  file_name: z.string(),
  container: z.string().optional(),
  sample_rate: z.number(),
  channels: z.number(),
  codec: z.string().nullable().optional(),
  duration_seconds: z.number(),
  file_size_bytes: z.number(),
  sha256: z.string(),
  created_at: z.string(),
  produced_by_job_id: z.string().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Pipeline run
// ---------------------------------------------------------------------------

export const pipelineStepSchema = z.object({
  type: z.string(),
  status: z.string(),
  job_id: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
});

export const pipelineRunSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source_type: z.string(),
  source_id: z.string(),
  profile_id: z.string().nullable().optional(),
  status: z.string(),
  steps: z.array(pipelineStepSchema),
  error: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  completed_at: z.string().nullable().optional(),
});

export const pipelineRunListResponseSchema = z.object({
  pipeline_runs: z.array(pipelineRunSchema),
});

export const pipelineRunDeleteResponseSchema = z.object({
  id: z.string(),
  deleted: z.boolean(),
});

// ---------------------------------------------------------------------------
// VOD transcriptions list
// ---------------------------------------------------------------------------

export const vodTranscriptionsResponseSchema = z.object({
  transcriptions: z.array(
    z.object({
      schema_version: z.number().optional(),
      id: z.string(),
      source_type: z.string(),
      source_id: z.string(),
      audio_artifact: z.string(),
      model: z.string(),
      device: z.string(),
      compute_type: z.string(),
      language: z.string().nullable().optional(),
      language_probability: z.number().nullable().optional(),
      duration_seconds: z.number(),
      created_at: z.string(),
      status: z.string().optional(),
      segment_count: z.number().optional(),
      files: z
        .object({
          json: z.string().optional(),
          txt: z.string().optional(),
          srt: z.string().optional(),
          vtt: z.string().optional(),
        })
        .optional(),
    }),
  ),
});
