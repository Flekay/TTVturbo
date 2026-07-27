export type FeatureStatus = "available" | "unavailable" | "not_implemented";

export interface StatusRecordings {
  count: number;
  total_duration_seconds: number;
  total_size_bytes: number;
}

export interface StatusStorage {
  free_bytes: number;
}

export interface StatusFeatures {
  recording: FeatureStatus;
  voice_cloning: FeatureStatus;
  voice_profiles?: FeatureStatus;
  twitch_profiles?: FeatureStatus;
  vod_downloader?: FeatureStatus;
  vod_pipeline?: FeatureStatus;
  audio_extraction?: FeatureStatus;
  transcription?: FeatureStatus;
  clip_finder?: FeatureStatus;
  vod_analysis: FeatureStatus;
  video_editor: FeatureStatus;
}

export interface VoiceCloneRuntime {
  available: boolean;
  device: string | null;
  torch_version: string | null;
  torch_cuda_version: string | null;
  cuda_available: boolean;
  device_name: string | null;
  vram_total_bytes: number | null;
  vram_free_bytes: number | null;
  qwen_tts_importable: boolean;
  reasons: string[];
  warnings: string[];
}

export interface VodPipelineAggregate {
  profiles: number;
  vods: number;
  ready: number;
  active: number;
  failed: number;
  downloaded_bytes: number;
}

export interface MediaProcessingAggregate {
  audio_artifacts: number;
  transcripts: number;
  audio_jobs: { total: number; ready: number; failed: number; active: number };
  transcription_jobs: { total: number; ready: number; failed: number; active: number };
  pipeline_runs: { total: number; active: number; ready_for_clip_analysis: number; failed: number };
}

export interface BackendStatus {
  status: "online" | "offline";
  app_name: string;
  version: string;
  uptime_seconds: number;
  recordings: StatusRecordings;
  storage: StatusStorage;
  features: StatusFeatures;
  voice_clone_runtime?: VoiceCloneRuntime;
  voice_profiles?: {
    count: number;
    clone_ready_count: number;
    complete_count: number;
  };
  vod_pipeline?: VodPipelineAggregate;
  media_processing?: MediaProcessingAggregate;
}
