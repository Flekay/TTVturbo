import { z } from "zod";

/**
 * ASR comparison feature Zod schemas.
 *
 * Mirror the backend responses from asr_api.py. Unknown additional
 * fields are stripped by Zod; permissive optional fields keep the UI
 * from crashing on future backend additions.
 */

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

export const asrPresetSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  model: z.string(),
  device: z.string(),
  compute_type: z.string(),
  task: z.string().optional(),
  language: z.string().nullable().optional(),
  multilingual: z.boolean().optional(),
  beam_size: z.number().optional(),
  word_timestamps: z.boolean().optional(),
  condition_on_previous_text: z.boolean().optional(),
  vad_filter: z.boolean().optional(),
  vad_parameters: z.record(z.string(), z.any()).optional(),
  hallucination_silence_threshold: z.number().nullable().optional(),
  hotwords: z.string().nullable().optional(),
  no_speech_threshold: z.number().nullable().optional(),
  log_prob_threshold: z.number().nullable().optional(),
  compression_ratio_threshold: z.number().nullable().optional(),
  production_eligible: z.boolean().optional(),
});

export const asrPresetListResponseSchema = z.object({
  presets: z.array(asrPresetSchema),
});

// ---------------------------------------------------------------------------
// Status / default
// ---------------------------------------------------------------------------

export const asrStatusSchema = z.object({
  running: z.boolean(),
  default_preset_id: z.string(),
  default_preset: asrPresetSchema,
  default_selected_at: z.string().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Benchmark
// ---------------------------------------------------------------------------

export const asrRunSummarySchema = z.object({
  preset_id: z.string(),
  preset_name: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  status: z.string(),
  runtime_seconds: z.number().nullable().optional(),
  model_load_seconds: z.number().nullable().optional(),
  peak_vram_mb: z.number().nullable().optional(),
  detected_language: z.string().nullable().optional(),
  language_probability: z.number().nullable().optional(),
  audio_duration_seconds: z.number().nullable().optional(),
  wer: z.number().nullable().optional(),
  cer: z.number().nullable().optional(),
  substitutions: z.number().nullable().optional(),
  deletions: z.number().nullable().optional(),
  insertions: z.number().nullable().optional(),
  metrics_available: z.boolean().nullable().optional(),
  hallucination_flag_count: z.number().nullable().optional(),
  missing_speech_flag_count: z.number().nullable().optional(),
  transcript_text: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  skip_reason: z.string().nullable().optional(),
});

export const asrBenchmarkSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source_type: z.string(),
  source_id: z.string(),
  source_duration_seconds: z.number().nullable().optional(),
  reference_text: z.string().nullable().optional(),
  hotwords: z.string().nullable().optional(),
  selected_presets: z.array(z.string()),
  status: z.string(),
  created_at: z.string(),
  completed_at: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  runs: z.array(asrRunSummarySchema),
});

export const asrBenchmarkListResponseSchema = z.object({
  benchmarks: z.array(asrBenchmarkSchema),
});

// ---------------------------------------------------------------------------
// Run detail (full segments, vad diagnosis, metrics, flags)
// ---------------------------------------------------------------------------

export const asrWordSchema = z.object({
  start: z.number(),
  end: z.number(),
  text: z.string(),
  probability: z.number().nullable().optional(),
});

export const asrSegmentSchema = z.object({
  id: z.number(),
  start: z.number(),
  end: z.number(),
  text: z.string(),
  avg_logprob: z.number().nullable().optional(),
  compression_ratio: z.number().nullable().optional(),
  no_speech_probability: z.number().nullable().optional(),
  words: z.array(asrWordSchema).optional(),
});

export const asrVadDiagnosisSchema = z.object({
  computed: z.boolean(),
  audio_duration_seconds: z.number().nullable().optional(),
  duration_after_vad_seconds: z.number().nullable().optional(),
  removed_by_vad_seconds: z.number().nullable().optional(),
  speech_regions: z.array(z.object({ start: z.number(), end: z.number() })).optional(),
});

export const asrFlagSchema = z.object({
  type: z.string(),
  severity: z.string(),
  segment_id: z.number().nullable().optional(),
  message: z.string(),
});

export const asrMetricsSchema = z.object({
  available: z.boolean(),
  reference_original: z.string().optional(),
  hypothesis_original: z.string().optional(),
  reference_normalised: z.string().optional(),
  hypothesis_normalised: z.string().optional(),
  wer: z.number().nullable().optional(),
  cer: z.number().nullable().optional(),
  mer: z.number().nullable().optional(),
  wil: z.number().nullable().optional(),
  wip: z.number().nullable().optional(),
  hits: z.number().nullable().optional(),
  substitutions: z.number().nullable().optional(),
  deletions: z.number().nullable().optional(),
  insertions: z.number().nullable().optional(),
  char_hits: z.number().nullable().optional(),
  char_substitutions: z.number().nullable().optional(),
  char_deletions: z.number().nullable().optional(),
  char_insertions: z.number().nullable().optional(),
  word_diff: z
    .array(
      z.object({
        type: z.string(),
        ref: z.array(z.string()).optional(),
        hyp: z.array(z.string()).optional(),
      }),
    )
    .optional(),
  error: z.string().nullable().optional(),
});

export const asrRunDetailSchema = z.object({
  schema_version: z.number().optional(),
  preset_id: z.string(),
  preset: asrPresetSchema.nullable().optional(),
  status: z.string(),
  faster_whisper_version: z.string().nullable().optional(),
  model_load_seconds: z.number().nullable().optional(),
  runtime_seconds: z.number().nullable().optional(),
  peak_vram_mb: z.number().nullable().optional(),
  audio_duration_seconds: z.number().nullable().optional(),
  detected_language: z.string().nullable().optional(),
  language_probability: z.number().nullable().optional(),
  all_language_probs: z.any().nullable().optional(),
  duration_after_vad_from_info: z.number().nullable().optional(),
  transcript_text: z.string().nullable().optional(),
  segments: z.array(asrSegmentSchema),
  effective_parameters: z.record(z.string(), z.any()).optional(),
  hotwords_used: z.string().nullable().optional(),
  metrics: asrMetricsSchema,
  vad_diagnosis: asrVadDiagnosisSchema,
  hallucination_flags: z.array(asrFlagSchema),
  missing_speech_flags: z.array(asrFlagSchema),
  error: z.string().nullable().optional(),
  created_at: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Default selection response
// ---------------------------------------------------------------------------

export const asrDefaultSelectionSchema = z.object({
  schema_version: z.number().optional(),
  preset_id: z.string(),
  preset: asrPresetSchema,
  selected_at: z.string().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Model candidates (multi-model comparison)
// ---------------------------------------------------------------------------

export const asrModelCandidateSchema = z.object({
  id: z.string(),
  model_family: z.string(),
  model_id: z.string(),
  name: z.string(),
  description: z.string(),
  options: z.record(z.string(), z.any()).optional(),
  production_eligible: z.boolean().optional(),
  diagnostic: z.boolean().optional(),
  available: z.boolean().optional(),
});

export const asrModelsResponseSchema = z.object({
  candidates: z.array(asrModelCandidateSchema),
  faster_whisper_available: z.boolean(),
  parakeet_available: z.boolean(),
  canary_available: z.boolean(),
  nemo_installed: z.boolean(),
  cuda_available: z.boolean(),
});

// ---------------------------------------------------------------------------
// Audio diagnostics (forensics)
// ---------------------------------------------------------------------------

export const asrAudioMetricsSchema = z.object({
  file_size_bytes: z.number().nullable().optional(),
  sha256: z.string().nullable().optional(),
  codec: z.string().nullable().optional(),
  sample_rate: z.number().nullable().optional(),
  channels: z.number().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  peak_dbfs: z.number().nullable().optional(),
  rms_dbfs: z.number().nullable().optional(),
  dc_offset: z.number().nullable().optional(),
  clipping_ratio: z.number().nullable().optional(),
  silence_ratio: z.number().nullable().optional(),
  speech_regions: z.array(z.object({ start: z.number(), end: z.number() })).optional(),
  speech_duration_seconds: z.number().nullable().optional(),
  quality_report: z.any().nullable().optional(),
  warnings: z.array(z.string()).optional(),
});

export const asrAudioArtifactSchema = z.object({
  filename: z.string(),
  metrics: asrAudioMetricsSchema.nullable().optional(),
  error: z.string().nullable().optional(),
});

export const asrAudioStreamSchema = z.object({
  index: z.number(),
  codec: z.string(),
  channels: z.number(),
  channel_layout: z.string().optional(),
  sample_rate: z.number(),
  bit_rate: z.number().nullable().optional(),
  language: z.string().nullable().optional(),
  title: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
});

export const asrAudioDiagnosticSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source_type: z.string(),
  source_id: z.string(),
  audio_stream_id: z.number().nullable().optional(),
  audio_streams: z.array(asrAudioStreamSchema),
  video_streams: z.array(z.any()).optional(),
  format: z.any().optional(),
  artifacts: z.record(z.string(), asrAudioArtifactSchema),
  created_at: z.string(),
});

export const asrAudioDiagnosticListResponseSchema = z.object({
  diagnostics: z.array(asrAudioDiagnosticSchema),
});

// ---------------------------------------------------------------------------
// Updated run summary with multi-model fields
// ---------------------------------------------------------------------------

export const asrRunSummaryV2Schema = asrRunSummarySchema.extend({
  candidate_id: z.string().nullable().optional(),
  model_family: z.string().nullable().optional(),
  model_reused: z.boolean().nullable().optional(),
  load_seconds: z.number().nullable().optional(),
  inference_seconds: z.number().nullable().optional(),
  total_seconds: z.number().nullable().optional(),
  peak_vram_bytes: z.number().nullable().optional(),
  peak_ram_bytes: z.number().nullable().optional(),
  audio_variant: z.string().nullable().optional(),
  warnings: z.array(z.string()).nullable().optional(),
  skip_reason: z.string().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Updated benchmark with candidate_ids and audio_variant
// ---------------------------------------------------------------------------

export const asrBenchmarkV2Schema = asrBenchmarkSchema.extend({
  candidate_ids: z.array(z.string()).nullable().optional(),
  audio_variant: z.string().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Create benchmark request (extended)
// ---------------------------------------------------------------------------

export const createBenchmarkRequestSchema = z.object({
  source_type: z.string(),
  source_id: z.string(),
  candidate_ids: z.array(z.string()).optional(),
  audio_variant: z.string().optional(),
  preset_ids: z.array(z.string()).optional(),
  reference_text: z.string().optional(),
  hotwords: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Create audio diagnostic request
// ---------------------------------------------------------------------------

export const createAudioDiagnosticRequestSchema = z.object({
  source_type: z.string(),
  source_id: z.string(),
  audio_stream_id: z.number().nullable().optional(),
});
