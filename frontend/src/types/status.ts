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
  vod_pipeline?: FeatureStatus;
  vod_analysis: FeatureStatus;
  video_editor: FeatureStatus;
  transcription?: FeatureStatus;
}

export interface VodPipelineAggregate {
  profiles: number;
  vods: number;
  ready: number;
  active: number;
  failed: number;
  downloaded_bytes: number;
}

export interface BackendStatus {
  status: "online" | "offline";
  app_name: string;
  version: string;
  uptime_seconds: number;
  recordings: StatusRecordings;
  storage: StatusStorage;
  features: StatusFeatures;
  voice_profiles?: {
    count: number;
    clone_ready_count: number;
    complete_count: number;
  };
  vod_pipeline?: VodPipelineAggregate;
}
