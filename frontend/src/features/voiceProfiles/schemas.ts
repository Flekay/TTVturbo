import { z } from "zod";

/**
 * Voice Profile feature Zod schemas.
 *
 * Every API response is validated at runtime so the UI never silently
 * consumes unknown JSON shapes. Unknown *additional* fields are tolerated
 * (Zod strips them by default), but missing required fields and unknown
 * enum values surface as controlled ApiErrors instead of React crashes.
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

export const voiceScriptSchema = z.object({
  id: z.string(),
  order: z.number(),
  style: z.string().optional(),
  category: z.string().optional(),
  text: z.string(),
  recommended_duration_seconds: z.number().nullable().optional(),
  notes: z.string().nullable().optional(),
});

export const voiceScriptPackSchema = z.object({
  scripts: z.array(voiceScriptSchema),
  total: z.number().optional(),
  locale: z.string().optional(),
});

export const voiceProfileReferenceSchema = z.object({
  script_id: z.string(),
  recording_filename: z.string(),
  status: referenceStatusSchema,
  created_at: z.string(),
  reviewed_at: z.string().nullable().optional(),
  rejection_reasons: z.array(z.string()).optional(),
  warnings: z.array(z.string()).optional(),
  // Technical quality is backend-defined; keep it as a passthrough object so
  // new fields do not break the client. Rendered best-effort.
  technical: z.record(z.string(), z.unknown()).optional(),
  quality: z.string().nullable().optional(),
});

export const voiceProfileProgressSchema = z.object({
  total: z.number(),
  accepted: z.number(),
  review: z.number(),
  rejected: z.number(),
  missing: z.number(),
  percent: z.number(),
  clone_ready: z.boolean(),
  pack_complete: z.boolean(),
});

export const voiceProfileSchema = z.object({
  id: z.string(),
  name: z.string(),
  locale: z.string(),
  created_at: z.string(),
  archived: z.boolean(),
  references: z.array(voiceProfileReferenceSchema),
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
  detail: z.union([z.string(), z.array(z.any())]),
});
