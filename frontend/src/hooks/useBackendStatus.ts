import { useStatusQuery } from "./useQueries";

export type ConnectionStatus = "online" | "offline" | "connecting";

export interface BackendStatusState {
  status: ConnectionStatus;
  data: ReturnType<typeof useStatusQuery>["data"] | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * Higher-level hook that derives a connection status from the TanStack Query
 * state. Used by the Sidebar and Topbar to show a single, consistent badge.
 */
export function useBackendStatus(): BackendStatusState {
  const query = useStatusQuery();
  let status: ConnectionStatus = "connecting";
  if (query.data && query.data.status === "online") status = "online";
  else if (query.isError) status = "offline";
  else if (query.data) status = "online";

  return {
    status,
    data: query.data ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: () => void query.refetch(),
  };
}
