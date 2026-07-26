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
  vod_analysis: FeatureStatus;
  video_editor: FeatureStatus;
}

export interface BackendStatus {
  status: "online" | "offline";
  app_name: string;
  version: string;
  uptime_seconds: number;
  recordings: StatusRecordings;
  storage: StatusStorage;
  features: StatusFeatures;
}
