import { apiClient } from "../../api/client";

export interface EditSource {
  id: string;
  media_item_id: string;
  asset_id?: string | null;
  sha256?: string;
  source_revision?: string | null;
}

export interface EditSequence {
  id: string;
  name: string;
  width: number;
  height: number;
  fps_numerator: number;
  fps_denominator: number;
  format_profile: string;
  tracks?: Record<string, TimelineTrack>;
  track_order?: string[];
  layout?: unknown;
}

export interface TimelineClip {
  id: string;
  source_media_item_id: string;
  source_asset_id?: string | null;
  source_start_us: number;
  source_end_us: number;
  timeline_start_us: number;
  transform?: {
    x?: number;
    y?: number;
    scale_x?: number;
    scale_y?: number;
    rotation?: number;
  };
  opacity?: number;
  [key: string]: unknown;
}

export interface TimelineTrack {
  id: string;
  type: string;
  name?: string;
  clips?: Record<string, TimelineClip>;
  [key: string]: unknown;
}

export interface EditBranch {
  id: string;
  name: string;
  head_commit_id: string;
  created_at?: string;
  updated_at?: string;
}

export interface EditProjectSummary {
  id: string;
  name: string;
  active_branch_id?: string | null;
  active_sequence_id?: string | null;
  detached_commit_id?: string | null;
  created_at: string;
  updated_at: string;
  branch_count: number;
  sequence_count: number;
}

export interface EditProject extends EditProjectSummary {
  sources: EditSource[];
  branches: EditBranch[];
  sequences: EditSequence[];
  checkout_commit_id: string;
  state_hash?: string;
}

export interface EditCommit {
  id: string;
  project_id?: string;
  author?: string | null;
  message: string;
  state_hash?: string;
  created_at: string;
  parent_ids?: string[];
  child_ids?: string[];
  operations?: Array<Record<string, unknown>>;
  state?: {
    sources?: EditSource[];
    sequences?: Record<string, EditSequence>;
  };
}

export async function fetchProjects(): Promise<EditProjectSummary[]> {
  const response = await apiClient.get<{ projects: EditProjectSummary[] }>("/api/edit-projects");
  return response.projects;
}

export async function fetchProject(projectId: string): Promise<EditProject> {
  return apiClient.get<EditProject>(`/api/edit-projects/${encodeURIComponent(projectId)}`);
}

export async function createProject(payload: {
  name: string;
  sources?: Array<{ media_item_id: string; asset_id?: string }>;
  sequences?: Array<{
    id?: string;
    name: string;
    width: number;
    height: number;
    fps_numerator: number;
    fps_denominator: number;
    format_profile: string;
  }>;
}): Promise<EditProject> {
  return apiClient.post<EditProject>("/api/edit-projects", { body: payload });
}

export async function deleteProject(projectId: string): Promise<void> {
  await apiClient.delete(`/api/edit-projects/${encodeURIComponent(projectId)}`);
}

export async function fetchCommits(projectId: string): Promise<EditCommit[]> {
  const response = await apiClient.get<{ commits: EditCommit[] }>(
    `/api/edit-projects/${encodeURIComponent(projectId)}/commits?limit=200`,
  );
  return response.commits;
}

export async function fetchCommitState(projectId: string, commitId: string): Promise<EditCommit> {
  return apiClient.get<EditCommit>(
    `/api/edit-projects/${encodeURIComponent(projectId)}/commits/${encodeURIComponent(commitId)}/state`,
  );
}


export async function addProjectSource(
  projectId: string,
  payload: {
    branch_id: string;
    expected_head_commit_id: string;
    source: { media_item_id: string; asset_id?: string };
    message?: string;
  },
): Promise<{ source: EditSource; commit: EditCommit }> {
  return apiClient.post<{ source: EditSource; commit: EditCommit }>(
    `/api/edit-projects/${encodeURIComponent(projectId)}/sources`,
    { body: payload },
  );
}

export async function createCommit(
  projectId: string,
  payload: {
    branch_id: string;
    expected_head_commit_id: string;
    message: string;
    operations: Array<Record<string, unknown>>;
  },
): Promise<EditCommit> {
  return apiClient.post<EditCommit>(`/api/edit-projects/${encodeURIComponent(projectId)}/commits`, {
    body: payload,
  });
}

export async function checkoutBranch(projectId: string, branchId: string): Promise<EditProject> {
  return apiClient.post<EditProject>(
    `/api/edit-projects/${encodeURIComponent(projectId)}/branches/${encodeURIComponent(branchId)}/checkout`,
  );
}

export async function checkoutSequence(projectId: string, sequenceId: string): Promise<EditProject> {
  return apiClient.post<EditProject>(
    `/api/edit-projects/${encodeURIComponent(projectId)}/sequences/${encodeURIComponent(sequenceId)}/checkout`,
  );
}

export async function createBranch(
  projectId: string,
  payload: { name: string; from_commit_id?: string },
): Promise<EditBranch> {
  return apiClient.post<EditBranch>(`/api/edit-projects/${encodeURIComponent(projectId)}/branches`, {
    body: payload,
  });
}

export async function checkoutCommit(projectId: string, commitId: string): Promise<EditProject> {
  return apiClient.post<EditProject>(`/api/edit-projects/${encodeURIComponent(projectId)}/checkout`, {
    body: { commit_id: commitId },
  });
}

export async function startRender(payload: {
  project_id: string;
  sequence_id: string;
  commit_id?: string;
  settings: Record<string, unknown>;
  output_lifecycle?: "TEMPORARY" | "PERSISTENT";
}): Promise<Record<string, unknown>> {
  return apiClient.post<Record<string, unknown>>("/api/rendering/jobs", { body: payload });
}

export interface EditorCommandContext {
  sequence?: { width?: number; height?: number } | null;
  playhead_seconds?: number | null;
  selected_clip?: {
    id?: string;
    transform?: { x?: number; y?: number; scale_x?: number; scale_y?: number; rotation?: number } | null;
    opacity?: number | null;
    speed?: number | null;
    audio_muted?: boolean | null;
    source_start_us?: number;
    source_end_us?: number;
    timeline_start_us?: number;
  } | null;
  tracks?: Array<{
    id: string;
    type?: string | null;
    name?: string | null;
    clip_count: number;
    selected?: boolean;
  }> | null;
}

export interface EditorCommandIntent {
  action: string;
  [key: string]: unknown;
}

export async function parseEditorCommand(
  command: string,
  context: EditorCommandContext,
): Promise<EditorCommandIntent> {
  const response = await apiClient.post<{ intent: EditorCommandIntent }>(
    "/api/editor-command/parse",
    { body: { command, context }, timeoutMs: 200_000 },
  );
  return response.intent;
}
