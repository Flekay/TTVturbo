import type { z } from "zod";
import type {
  asrPresetSchema,
  asrPresetListResponseSchema,
  asrStatusSchema,
  asrBenchmarkSchema,
  asrBenchmarkListResponseSchema,
  asrRunDetailSchema,
  asrDefaultSelectionSchema,
  asrModelCandidateSchema,
  asrModelsResponseSchema,
  asrAudioDiagnosticSchema,
  asrAudioDiagnosticListResponseSchema,
  asrAudioMetricsSchema,
  asrAudioStreamSchema,
  createBenchmarkRequestSchema,
  createAudioDiagnosticRequestSchema,
} from "./schemas";

export type AsrPreset = z.infer<typeof asrPresetSchema>;
export type AsrPresetListResponse = z.infer<typeof asrPresetListResponseSchema>;
export type AsrStatus = z.infer<typeof asrStatusSchema>;
export type AsrBenchmark = z.infer<typeof asrBenchmarkSchema>;
export type AsrBenchmarkListResponse = z.infer<typeof asrBenchmarkListResponseSchema>;
export type AsrRunDetail = z.infer<typeof asrRunDetailSchema>;
export type AsrDefaultSelection = z.infer<typeof asrDefaultSelectionSchema>;
export type AsrModelCandidate = z.infer<typeof asrModelCandidateSchema>;
export type AsrModelsResponse = z.infer<typeof asrModelsResponseSchema>;
export type AsrAudioDiagnostic = z.infer<typeof asrAudioDiagnosticSchema>;
export type AsrAudioDiagnosticListResponse = z.infer<typeof asrAudioDiagnosticListResponseSchema>;
export type AsrAudioMetrics = z.infer<typeof asrAudioMetricsSchema>;
export type AsrAudioStream = z.infer<typeof asrAudioStreamSchema>;
export type CreateBenchmarkRequest = z.infer<typeof createBenchmarkRequestSchema>;
export type CreateAudioDiagnosticRequest = z.infer<typeof createAudioDiagnosticRequestSchema>;

export interface SelectDefaultRequest {
  preset_id: string;
}

export const AUDIO_VARIANTS = [
  "current-asr-input",
  "left-channel",
  "right-channel",
  "mono-current",
  "mono-average",
] as const;
export type AudioVariant = (typeof AUDIO_VARIANTS)[number];
