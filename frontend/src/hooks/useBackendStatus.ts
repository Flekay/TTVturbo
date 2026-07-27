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
 * state. Used by the Topbar to show a single, consistent badge.
 *
 * Error check comes first: when a refetch fails, TanStack Query keeps the
 * last successful ``data`` around, so checking ``data`` before ``isError``
 * would leave the badge stuck on "online" even though the backend is
 * unreachable.
 */
export function useBackendStatus(): BackendStatusState {
  const query = useStatusQuery();
  let status: ConnectionStatus = "connecting";
  if (query.isError) status = "offline";
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
