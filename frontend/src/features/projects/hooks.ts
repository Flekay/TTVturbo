import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  checkoutBranch,
  checkoutCommit,
  checkoutSequence,
  addProjectSource,
  createBranch,
  createCommit,
  createProject,
  deleteProject,
  fetchCommits,
  fetchCommitState,
  fetchProject,
  fetchProjects,
  resetBranch,
  type EditCommitPage,
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
  return useInfiniteQuery({
    queryKey: ["projects", projectId, "commits"],
    queryFn: ({ pageParam }: { pageParam: number }) => fetchCommits(projectId!, { limit: 10, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage: EditCommitPage) => {
      const nextOffset = lastPage.offset + lastPage.commits.length;
      return nextOffset < lastPage.total ? nextOffset : undefined;
    },
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


export function useAddProjectSource(projectId: string) {
  return useProjectMutation(
    (payload: Parameters<typeof addProjectSource>[1]) => addProjectSource(projectId, payload),
    projectId,
  );
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

export function useResetBranch(projectId: string) {
  return useProjectMutation(
    (payload: Parameters<typeof resetBranch>[1]) => resetBranch(projectId, payload),
    projectId,
  );
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
