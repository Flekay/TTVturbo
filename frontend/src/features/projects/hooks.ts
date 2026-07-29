import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  checkoutBranch,
  checkoutCommit,
  checkoutSequence,
  createBranch,
  createCommit,
  createProject,
  deleteProject,
  fetchCommits,
  fetchCommitState,
  fetchProject,
  fetchProjects,
} from "./api";

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: fetchProjects });
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => fetchProject(projectId!),
    enabled: Boolean(projectId),
  });
}

export function useProjectCommits(projectId: string | undefined) {
  return useQuery({
    queryKey: ["projects", projectId, "commits"],
    queryFn: () => fetchCommits(projectId!),
    enabled: Boolean(projectId),
  });
}

export function useCommitState(projectId: string | undefined, commitId: string | undefined) {
  return useQuery({
    queryKey: ["projects", projectId, "commit", commitId],
    queryFn: () => fetchCommitState(projectId!, commitId!),
    enabled: Boolean(projectId && commitId),
  });
}

function useProjectMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>,
  projectId?: string,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["projects"] });
      if (projectId) client.invalidateQueries({ queryKey: ["projects", projectId] });
    },
  });
}

export function useCreateProject() {
  return useProjectMutation(createProject);
}

export function useDeleteProject() {
  return useProjectMutation(deleteProject);
}

export function useCreateCommit(projectId: string) {
  return useProjectMutation(
    (payload: Parameters<typeof createCommit>[1]) => createCommit(projectId, payload),
    projectId,
  );
}

export function useCheckoutBranch(projectId: string) {
  return useProjectMutation((branchId: string) => checkoutBranch(projectId, branchId), projectId);
}

export function useCheckoutSequence(projectId: string) {
  return useProjectMutation((sequenceId: string) => checkoutSequence(projectId, sequenceId), projectId);
}

export function useCheckoutCommit(projectId: string) {
  return useProjectMutation((commitId: string) => checkoutCommit(projectId, commitId), projectId);
}

export function useCreateBranch(projectId: string) {
  return useProjectMutation(
    (payload: Parameters<typeof createBranch>[1]) => createBranch(projectId, payload),
    projectId,
  );
}
