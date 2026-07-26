import { z } from "zod";

/**
 * Voice Profile feature Zod schemas.
 *
 * Every API response is validated at runtime so the UI never silently
 * consumes unknown JSON shapes. The schemas mirror the actual backend
 * responses from the FastAPI voice-profile endpoints (see
 * voice_profiles_api.py / voice_profiles service). Unknown *additional*
 * fields are tolerated (Zod strips them by default), but missing required
 * fields and unknown enum values surface as controlled ApiErrors instead
 * of React crashes.
 *
 * Reference statuses follow the same permissive convention used for
 * generation statuses in `types/schemas.ts`: the schema accepts any string
 * so a future backend status renders as a neutral badge instead of
 * throwing. KNOWN_REFERENCE_STATUSES lists the values the UI understands.
 */

export const KNOWN_REFERENCE_STATUSES = [
  "ACCEPTED",
  "REVIEW",
  "REJECTED",
] as const;

export type KnownReferenceStatus = (typeof KNOWN_REFERENCE_STATUSES)[number];

/** Permissive status: known values get coloured badges, unknown ones neutral. */
export const referenceStatusSchema = z.string();

/** Quality class reported by the real voice_clone.quality analyzer. */
export const qualityClassSchema = z.string();

const recommendedDurationSchema = z.object({
  min: z.number(),
  max: z.number(),
});

/** A single recording prompt from the script pack / holdout. */
export const voiceScriptSchema = z.object({
  id: z.string(),
  order: z.number(),
  category: z.string(),
  style: z.string(),
  text: z.string(),
  recommended_duration_seconds: recommendedDurationSchema,
  tags: z.array(z.string()),
  recording_notes: z.string().nullable().optional(),
});

/** Pack metadata returned alongside the prompt list. */
export const voiceScriptPackMetaSchema = z.object({
  pack_id: z.string(),
  locale: z.string(),
  title: z.string().optional(),
  prompt_count: z.number(),
});

/** Response shape of GET /scripts and GET /holdout-scripts. */
export const voiceScriptPackSchema = z.object({
  pack: voiceScriptPackMetaSchema,
  prompts: z.array(voiceScriptSchema),
});

/** A reference attached to a profile. The backend stores references as a
 * dict keyed by script_id; the API returns that dict directly. */
export const voiceProfileReferenceSchema = z.object({
  script_id: z.string(),
  script_text: z.string(),
  category: z.string(),
  style: z.string(),
  recording_filename: z.string(),
  recording_sha256: z.string(),
  quality: z.record(z.string(), z.unknown()),
  quality_class: qualityClassSchema,
  status: referenceStatusSchema,
  review_accepted: z.boolean(),
  attached_at: z.string(),
  updated_at: z.string(),
});

/** Derived progress for a profile. Computed server-side from the script
 * library + stored references; never persisted as a stale counter. */
export const voiceProfileProgressSchema = z.object({
  total: z.number(),
  missing: z.number(),
  recorded: z.number(),
  accepted: z.number(),
  review: z.number(),
  rejected: z.number(),
  percentage: z.number(),
  clone_ready: z.boolean(),
  pack_complete: z.boolean(),
});

/** A persisted voice profile. References are a dict keyed by script_id. */
export const voiceProfileSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  name: z.string(),
  locale: z.string(),
  created_at: z.string(),
  updated_at: z.string().optional(),
  references: z.record(z.string(), voiceProfileReferenceSchema),
  progress: voiceProfileProgressSchema,
});

export const voiceProfileListResponseSchema = z.object({
  profiles: z.array(voiceProfileSchema),
});

export const voiceProfileDeleteResponseSchema = z.object({
  id: z.string(),
  deleted: z.boolean(),
});

/** Best-effort error response schema. FastAPI errors return `{detail: ...}`. */
export const apiErrorSchema = z.object({
  detail: z.union([z.string(), z.array(z.any()), z.record(z.string(), z.any())]),
});
