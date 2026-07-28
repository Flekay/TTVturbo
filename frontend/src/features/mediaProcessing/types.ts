import type { z } from "zod";
import type {
  transcriptionRuntimeStatusSchema,
  mediaJobSchema,
  transcriptionListResponseSchema,
  audioArtifactSchema,
  pipelineStepSchema,
  pipelineSourceSchema,
  pipelineRunSchema,
  pipelineRunListResponseSchema,
  pipelineRunDeleteResponseSchema,
  vodTranscriptionsResponseSchema,
  transcriptViewSchema,
  transcriptSegmentSchema,
  transcriptRevisionEntrySchema,
  transcriptRevisionsResponseSchema,
  conversationMiningRuntimeStatusSchema,
  conversationMiningBlockSchema,
  conversationMiningModelSchema,
  conversationSchema,
  miningRunSchema,
  miningRunListResponseSchema,
  miningRunDeleteResponseSchema,
} from "./schemas";

export type TranscriptionRuntimeStatus = z.infer<typeof transcriptionRuntimeStatusSchema>;
export type MediaJob = z.infer<typeof mediaJobSchema>;
export type TranscriptionListResponse = z.infer<typeof transcriptionListResponseSchema>;
export type AudioArtifact = z.infer<typeof audioArtifactSchema>;
export type PipelineStep = z.infer<typeof pipelineStepSchema>;
export type PipelineSource = z.infer<typeof pipelineSourceSchema>;
export type PipelineRun = z.infer<typeof pipelineRunSchema>;
export type PipelineRunListResponse = z.infer<typeof pipelineRunListResponseSchema>;
export type PipelineRunDeleteResponse = z.infer<typeof pipelineRunDeleteResponseSchema>;
export type VodTranscriptionsResponse = z.infer<typeof vodTranscriptionsResponseSchema>;
export type TranscriptView = z.infer<typeof transcriptViewSchema>;
export type TranscriptSegment = z.infer<typeof transcriptSegmentSchema>;
export type TranscriptRevisionEntry = z.infer<typeof transcriptRevisionEntrySchema>;
export type TranscriptRevisionsResponse = z.infer<typeof transcriptRevisionsResponseSchema>;
export type ConversationMiningRuntimeStatus = z.infer<typeof conversationMiningRuntimeStatusSchema>;
export type ConversationMiningBlock = z.infer<typeof conversationMiningBlockSchema>;
export type ConversationMiningModel = z.infer<typeof conversationMiningModelSchema>;
export type Conversation = z.infer<typeof conversationSchema>;
export type MiningRun = z.infer<typeof miningRunSchema>;
export type MiningRunListResponse = z.infer<typeof miningRunListResponseSchema>;
export type MiningRunDeleteResponse = z.infer<typeof miningRunDeleteResponseSchema>;

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

export interface StartPipelineRunFromUrlRequest {
  url: string;
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

export interface StartMiningRunRequest {
  media_item_id: string;
  force?: boolean;
}
