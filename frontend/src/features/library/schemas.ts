import { z } from "zod";

export const uploadItemSchema = z.object({
  id: z.string(),
  source_type: z.string(),
  title: z.string(),
  file_name: z.string(),
  duration_seconds: z.number().nullable().optional(),
  status: z.string(),
  file_size_bytes: z.number().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const uploadListResponseSchema = z.object({
  uploads: z.array(uploadItemSchema),
});

export type UploadItem = z.infer<typeof uploadItemSchema>;
export type UploadListResponse = z.infer<typeof uploadListResponseSchema>;
