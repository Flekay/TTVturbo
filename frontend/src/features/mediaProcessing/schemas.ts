import { z } from "zod";

/**
 * Media Processing feature Zod schemas.
 *
 * Mirror the backend responses from media_processing_api.py and
 * conversation_mining_api.py. Unknown additional fields are stripped by
 * Zod; status values are permissive strings so a future backend status
 * renders as a neutral badge instead of throwing.
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
  // Additive v2 fields (old runs default to null/0/[]).
  progress: z.number().nullable().optional(),
  message: z.string().nullable().optional(),
  attempt: z.number().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  artifact_ids: z.array(z.string()).optional(),
});

export const pipelineSourceSchema = z.object({
  provider: z.string().optional(),
  type: z.string().nullable().optional(),
  external_id: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  profile_id: z.string().nullable().optional(),
  title: z.string().nullable().optional(),
  thumbnail_url: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  legacy: z.boolean().optional(),
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
  // Additive v2 fields (old runs default to null).
  source: pipelineSourceSchema.nullable().optional(),
  progress: z.number().nullable().optional(),
  current_step: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  library_item_id: z.string().nullable().optional(),
  transcript_id: z.string().nullable().optional(),
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

// ---------------------------------------------------------------------------
// Editable transcript (schema_version 2 view) + corrections
// ---------------------------------------------------------------------------

export const transcriptSegmentSchema = z.object({
  id: z.string(),
  start: z.number(),
  end: z.number(),
  raw_text: z.string(),
  corrected_text: z.string().nullable().optional(),
  avg_logprob: z.number().nullable().optional(),
  no_speech_probability: z.number().nullable().optional(),
  words: z.array(z.any()).optional(),
});

export const transcriptViewSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source_type: z.string().nullable().optional(),
  source_id: z.string().nullable().optional(),
  media_item_id: z.string().nullable().optional(),
  audio_artifact: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  device: z.string().nullable().optional(),
  compute_type: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
  language_probability: z.number().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string().optional(),
  revision: z.number(),
  correction_status: z.string(),
  raw_text: z.string(),
  corrected_text: z.string().nullable().optional(),
  engine: z
    .object({
      family: z.string(),
      model: z.string().nullable().optional(),
      language: z.string().nullable().optional(),
    })
    .optional(),
  segments: z.array(transcriptSegmentSchema),
});

// ---------------------------------------------------------------------------
// Conversation Mining
// ---------------------------------------------------------------------------

export const conversationMiningRuntimeStatusSchema = z.object({
  available: z.boolean(),
  model: z.string(),
  device: z.string(),
  dtype: z.string().optional(),
  busy: z.boolean().optional(),
  busy_owner_type: z.string().nullable().optional(),
  reasons: z.array(z.string()),
});

export const conversationMiningBlockSchema = z.object({
  block_id: z.string(),
  start: z.number(),
  end: z.number(),
  status: z.string(),
  attempt: z.number().optional(),
  model_input_segments: z.number().optional(),
  result_count: z.number().nullable().optional(),
  error: z.string().nullable().optional(),
});

export const conversationMiningModelSchema = z.object({
  provider: z.string(),
  model_id: z.string(),
  revision: z.string().nullable().optional(),
});

export const conversationSchema = z.object({
  id: z.string(),
  start: z.number(),
  end: z.number(),
  start_segment_id: z.string().optional(),
  end_segment_id: z.string().optional(),
  title: z.string(),
  summary: z.string(),
  topic: z.string().nullable().optional(),
  category: z.string(),
  transcript_excerpt: z.string().optional(),
  excerpt_has_corrected: z.boolean().optional(),
  signals: z.array(z.string()),
  context: z
    .object({
      requires_previous_context: z.boolean(),
      requires_following_context: z.boolean(),
    })
    .optional(),
  confidence: z.number(),
});

export const miningRunSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  media_item_id: z.string(),
  transcript_id: z.string(),
  transcript_revision: z.number().optional(),
  status: z.string(),
  model: conversationMiningModelSchema.nullable().optional(),
  mining_config_version: z.number().optional(),
  created_at: z.string(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  blocks: z.array(conversationMiningBlockSchema).optional(),
  conversations: z.array(conversationSchema).optional(),
  progress: z.number().optional(),
  current_block: z.string().nullable().optional(),
});

export const miningRunListResponseSchema = z.object({
  runs: z.array(miningRunSchema),
});

export const miningRunDeleteResponseSchema = z.object({
  id: z.string(),
  deleted: z.boolean(),
});

export const startMiningRunRequestSchema = z.object({
  media_item_id: z.string(),
  force: z.boolean().optional(),
});

export const transcriptRevisionChangeSchema = z.object({
  segment_id: z.string(),
  before: z.string().nullable().optional(),
  after: z.string().nullable().optional(),
});

export const transcriptRevisionEntrySchema = z.object({
  revision: z.number(),
  created_at: z.string(),
  changes: z.array(transcriptRevisionChangeSchema),
});

export const transcriptRevisionsResponseSchema = z.object({
  revisions: z.array(transcriptRevisionEntrySchema),
});
