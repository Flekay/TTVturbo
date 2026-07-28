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
  transcriptViewSchema,
  transcriptSegmentSchema,
  transcriptRevisionEntrySchema,
  transcriptRevisionsResponseSchema,
} from "./schemas";

export type TranscriptionRuntimeStatus = z.infer<typeof transcriptionRuntimeStatusSchema>;
export type MediaJob = z.infer<typeof mediaJobSchema>;
export type TranscriptionListResponse = z.infer<typeof transcriptionListResponseSchema>;
export type AudioArtifact = z.infer<typeof audioArtifactSchema>;
export type PipelineRun = z.infer<typeof pipelineRunSchema>;
export type PipelineRunListResponse = z.infer<typeof pipelineRunListResponseSchema>;
export type PipelineRunDeleteResponse = z.infer<typeof pipelineRunDeleteResponseSchema>;
export type VodTranscriptionsResponse = z.infer<typeof vodTranscriptionsResponseSchema>;
export type TranscriptView = z.infer<typeof transcriptViewSchema>;
export type TranscriptSegment = z.infer<typeof transcriptSegmentSchema>;
export type TranscriptRevisionEntry = z.infer<typeof transcriptRevisionEntrySchema>;
export type TranscriptRevisionsResponse = z.infer<typeof transcriptRevisionsResponseSchema>;

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

export interface SegmentCorrectionInput {
  segment_id: string;
  corrected_text: string | null;
}

export interface SaveCorrectionsRequest {
  expected_revision: number;
  segments: SegmentCorrectionInput[];
}

/** Error body returned by the corrections API on a revision conflict (409). */
export interface RevisionConflictDetail {
  code: "revision_conflict";
  message: string;
  current_revision: number;
  transcript?: TranscriptView;
}
