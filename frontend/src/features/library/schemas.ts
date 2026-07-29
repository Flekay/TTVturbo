import { z } from "zod";

export const libraryItemSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source: z.enum(["vod", "upload"]),
  title: z.string(),
  file_name: z.string(),
  file_size_bytes: z.number().nullable().optional(),
  file_exists: z.boolean().optional(),
  duration_seconds: z.number().nullable().optional(),
  container: z.string().nullable().optional(),
  twitch_video_id: z.string().nullable().optional(),
  vod_id: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  lifecycle: z.enum(["TEMPORARY", "PERSISTENT"]).optional(),
  expires_at: z.string().nullable().optional(),
  derived: z.boolean().optional(),
  derived_from_item_id: z.string().nullable().optional(),
  generated: z.boolean().optional(),
});

export const libraryItemListResponseSchema = z.object({
  items: z.array(libraryItemSchema),
});

export type LibraryItem = z.infer<typeof libraryItemSchema>;
export type LibraryItemListResponse = z.infer<typeof libraryItemListResponseSchema>;
