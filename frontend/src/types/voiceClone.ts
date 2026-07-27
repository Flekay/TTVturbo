import type { KnownGenerationStatus } from "./schemas";

/**
 * Generation status. The known statuses are typed explicitly; unknown future
 * statuses are still representable as strings so the UI can render a neutral
 * badge instead of crashing.
 */
export type GenerationStatus = KnownGenerationStatus | (string & {});

export type QualityClass = "EXCELLENT" | "GOOD" | "REVIEW" | "REJECT";

export interface VoiceCloneStatusResponse {
  available: boolean;
  busy: boolean;
  active_generation_id: string | null;
  model_id: string;
  // Optional extended runtime fields.
  device?: string | null;
  device_name?: string | null;
  torch_version?: string | null;
  cuda_available?: boolean;
  qwen_tts_importable?: boolean;
  model_cached?: boolean;
  reasons?: string[];
  warnings?: string[];
}

export interface QualityMetrics {
  technical: {
    sample_rate: number;
    channels: number;
    frame_count: number;
    duration_seconds: number;
    subtype: string | null;
    format: string | null;
  };
  levels: {
    peak_dbfs: number | null;
    rms_dbfs: number | null;
    dc_offset: number;
    clipping_sample_count: number;
    clipping_sample_ratio: number;
  };
  silence: {
    leading_silence_ms: number;
    trailing_silence_ms: number;
    total_silence_ratio: number;
    voice_ratio: number;
    frame_count_total: number;
    frame_count_silent: number;
    frame_count_active: number;
  };
  noise: {
    estimated_noise_floor_dbfs: number | null;
    estimated_snr_db: number | null;
    active_frames_used: number;
  };
  dropouts: {
    dropout_count: number;
    dropout_total_ms: number;
    longest_dropout_ms: number;
  };
  integrity: {
    has_nan: boolean;
    has_infinity: boolean;
  };
  quality: QualityClass;
  reasons: string[];
  warnings: string[];
  voice_clone_reference: {
    eligible: boolean;
    quality: QualityClass;
    reasons: string[];
    warnings: string[];
  };
}

export interface GenerationMetadata {
  id: string;
  status: GenerationStatus;
  reference_recording: string;
  reference_sha256: string;
  reference_text: string;
  target_text: string;
  language: string;
  model_id: string;
  model_revision: string;
  created_at: string;
  completed_at: string | null;
  output_duration_seconds: number | null;
  generation_seconds: number | null;
  peak_vram_bytes: number | null;
  quality: Partial<QualityMetrics> & { quality?: QualityClass };
  failure_reason: string | null;
  warnings: string[];
  // Optional technical details, present only when the backend supplies them.
  // Field names must match voice_clone/schemas.py GenerationMetadata exactly.
  output_sha256?: string | null;
  output_sample_rate?: number | null;
  worker_exit_code?: number | null;
  device_name?: string | null;
  // Voice-profile mode metadata. Present only for generations started from
  // an accepted profile reference. Older generations do not have these.
  voice_profile_id?: string | null;
  voice_profile_name?: string | null;
  voice_profile_script_id?: string | null;
}

export interface GenerationListResponse {
  generations: GenerationMetadata[];
}

export interface CreateGenerationRequest {
  // Manual (legacy) mode.
  reference_recording?: string;
  reference_text?: string;
  target_text: string;
  language?: string;
  allow_quality_warning?: boolean;
  // Profile mode (mutually exclusive with manual fields).
  voice_profile_id?: string;
  voice_profile_script_id?: string;
}

export interface CreateGenerationResponse {
  id: string;
  status: GenerationStatus;
}

export interface DeleteGenerationResponse {
  id: string;
  deleted: boolean;
}
