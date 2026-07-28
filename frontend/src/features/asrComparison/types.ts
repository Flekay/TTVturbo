import type { z } from "zod";
import type {
  asrPresetSchema,
  asrPresetListResponseSchema,
  asrStatusSchema,
  asrBenchmarkSchema,
  asrBenchmarkListResponseSchema,
  asrRunDetailSchema,
  asrDefaultSelectionSchema,
} from "./schemas";

export type AsrPreset = z.infer<typeof asrPresetSchema>;
export type AsrPresetListResponse = z.infer<typeof asrPresetListResponseSchema>;
export type AsrStatus = z.infer<typeof asrStatusSchema>;
export type AsrBenchmark = z.infer<typeof asrBenchmarkSchema>;
export type AsrBenchmarkListResponse = z.infer<typeof asrBenchmarkListResponseSchema>;
export type AsrRunDetail = z.infer<typeof asrRunDetailSchema>;
export type AsrDefaultSelection = z.infer<typeof asrDefaultSelectionSchema>;

export interface CreateBenchmarkRequest {
  source_type: string;
  source_id: string;
  preset_ids: string[];
  reference_text?: string;
  hotwords?: string;
}

export interface SelectDefaultRequest {
  preset_id: string;
}
