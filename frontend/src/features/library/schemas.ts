import { z } from "zod";

export const FILE_TYPES = ["video", "audio", "image"] as const;
export type FileType = (typeof FILE_TYPES)[number];

export const libraryItemSchema = z.object({
  schema_version: z.number().optional(),
  id: z.string(),
  source: z.enum(["vod", "upload"]),
  title: z.string(),
  file_name: z.string(),
  file_size_bytes: z.number().nullable().optional(),
  file_exists: z.boolean().optional(),
  duration_seconds: z.number().nullable().optional(),
  file_type: z.enum(FILE_TYPES).nullable().optional(),
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

/** Extensions accepted by the library upload input, grouped by type. */
export const ACCEPTED_UPLOAD_EXTENSIONS: Record<FileType, string> = {
  video: ".mp4,.mkv,.webm,.mov",
  audio: ".mp3,.wav,.flac,.ogg,.m4a,.aac,.opus",
  image: ".png,.jpg,.jpeg,.webp,.gif,.bmp,.svg",
};

/** Combined accept string for the upload <input>. */
export const ACCEPTED_UPLOAD_ALL = Object.values(ACCEPTED_UPLOAD_EXTENSIONS).join(",");

