import type { z } from "zod";
import type {
  voiceScriptSchema,
  voiceScriptPackSchema,
  voiceProfileReferenceSchema,
  voiceProfileProgressSchema,
  voiceProfileSchema,
  voiceProfileListResponseSchema,
  voiceProfileDeleteResponseSchema,
  apiErrorSchema,
  KnownReferenceStatus,
} from "./schemas";

export type VoiceScript = z.infer<typeof voiceScriptSchema>;
export type VoiceScriptPack = z.infer<typeof voiceScriptPackSchema>;
export type VoiceProfileReference = z.infer<typeof voiceProfileReferenceSchema>;
export type VoiceProfileProgress = z.infer<typeof voiceProfileProgressSchema>;
export type VoiceProfile = z.infer<typeof voiceProfileSchema>;
export type VoiceProfileListResponse = z.infer<typeof voiceProfileListResponseSchema>;
export type VoiceProfileDeleteResponse = z.infer<typeof voiceProfileDeleteResponseSchema>;
export type ApiErrorResponse = z.infer<typeof apiErrorSchema>;

export type ReferenceStatus = KnownReferenceStatus | string;

/** Filter values for the prompt browser. */
export type PromptFilter =
  | "ALL"
  | "MISSING"
  | "ACCEPTED"
  | "REVIEW"
  | "REJECTED";

/** Request handed to the integrator's recorder when "Jetzt aufnehmen" is clicked. */
export type PromptRecordingRequest = {
  profileId: string;
  scriptId: string;
  scriptText: string;
};

export type VoiceProfilesPanelProps = {
  onStartPromptRecording?: (request: PromptRecordingRequest) => void;
};

/** Payloads for mutations. The server knows the script text by id, so the
 * client never sends script text bodies. */
export type CreateVoiceProfileRequest = {
  name: string;
  locale: string;
};

export type PatchVoiceProfileRequest = {
  name?: string;
  archived?: boolean;
};

export type AttachReferenceRequest = {
  recording_filename: string;
};
