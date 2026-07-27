import type { z } from "zod";
import type {
  twitchProfileSchema,
  twitchProfileListResponseSchema,
  twitchVodSchema,
  vodListResponseSchema,
  vodSyncResponseSchema,
  vodDeleteResponseSchema,
  twitchStatusSchema,
  vodLogResponseSchema,
  vodStatusSchema,
} from "./schemas";

export type VodStatus = z.infer<typeof vodStatusSchema>;
export type TwitchProfile = z.infer<typeof twitchProfileSchema>;
export type TwitchProfileListResponse = z.infer<typeof twitchProfileListResponseSchema>;
export type TwitchVod = z.infer<typeof twitchVodSchema>;
export type VodListResponse = z.infer<typeof vodListResponseSchema>;
export type VodSyncResponse = z.infer<typeof vodSyncResponseSchema>;
export type VodDeleteResponse = z.infer<typeof vodDeleteResponseSchema>;
export type TwitchStatus = z.infer<typeof twitchStatusSchema>;
export type VodLogResponse = z.infer<typeof vodLogResponseSchema>;

export interface CreateProfileRequest {
  login?: string;
  url?: string;
}

export interface ImportVodRequest {
  profile_id?: string | null;
  url: string;
}

export interface ListVodsParams {
  profile_id?: string;
  status?: string;
  search?: string;
  sort?: "newest" | "oldest" | "longest" | "shortest";
}

// Re-export the known statuses for consumers.
export { KNOWN_VOD_STATUSES } from "./schemas";
export type { KnownVodStatus } from "./schemas";
