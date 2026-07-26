import "./voiceProfiles.css";
import { VoiceProfilesPanel } from "./VoiceProfilesPanel";

export { VoiceProfilesPanel } from "./VoiceProfilesPanel";
export type {
  PromptRecordingRequest,
  VoiceProfilesPanelProps,
  VoiceProfile,
  VoiceProfileListResponse,
  VoiceProfileProgress,
  VoiceProfileReference,
  VoiceScript,
  VoiceScriptPack,
  ReferenceStatus,
  PromptFilter,
  CreateVoiceProfileRequest,
  PatchVoiceProfileRequest,
  AttachReferenceRequest,
} from "./types";
export {
  KNOWN_REFERENCE_STATUSES,
  voiceScriptSchema,
  voiceScriptPackSchema,
  voiceProfileSchema,
  voiceProfileListResponseSchema,
  voiceProfileReferenceSchema,
  voiceProfileProgressSchema,
  voiceProfileDeleteResponseSchema,
  apiErrorSchema,
} from "./schemas";
export {
  voiceProfilesQueryKey,
  voiceProfileQueryKey,
  voiceProfileScriptsQueryKey,
  voiceProfileHoldoutsQueryKey,
} from "./hooks";

// Re-export the default component for convenience.
export default VoiceProfilesPanel;
