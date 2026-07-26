export interface Recording {
  filename: string;
  created_at: string;
  duration_seconds: number;
  file_size_bytes: number;
  audio_url: string;
}

export interface RecordingListResponse {
  recordings: Recording[];
}

export interface RecordingUploadResponse {
  filename: string;
  url: string;
  size_bytes: number;
  probe?: string;
}

export interface RecordingDeleteResponse {
  filename: string;
  deleted: boolean;
}
