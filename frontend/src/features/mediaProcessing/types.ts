import type { z } from "zod";
import type {
  transcriptionRuntimeStatusSchema,
  mediaJobSchema,
  transcriptionListResponseSchema,
  audioArtifactSchema,
  pipelineRunSchema,
  pipelineRunListResponseSchema,
  pipelineRunDeleteResponseSchema,
  vodTranscriptionsResponseSchema,
} from "./schemas";

export type TranscriptionRuntimeStatus = z.infer<typeof transcriptionRuntimeStatusSchema>;
export type MediaJob = z.infer<typeof mediaJobSchema>;
export type TranscriptionListResponse = z.infer<typeof transcriptionListResponseSchema>;
export type AudioArtifact = z.infer<typeof audioArtifactSchema>;
export type PipelineRun = z.infer<typeof pipelineRunSchema>;
export type PipelineRunListResponse = z.infer<typeof pipelineRunListResponseSchema>;
export type PipelineRunDeleteResponse = z.infer<typeof pipelineRunDeleteResponseSchema>;
export type VodTranscriptionsResponse = z.infer<typeof vodTranscriptionsResponseSchema>;

export interface StartTranscriptionRequest {
  source_type?: string;
  source_id: string;
  language?: string;
  model?: string;
  model_family?: string; // "whisper" | "parakeet" | "canary"
  hotwords?: string;
  force_audio_extraction?: boolean;
}

export interface StartPipelineRunRequest {
  source_type?: string;
  source_id: string;
}

export interface StartAudioExtractionRequest {
  force?: boolean;
}
