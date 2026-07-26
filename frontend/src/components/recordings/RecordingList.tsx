import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { Recording } from "../../types/recording";
import { useRecordingsQuery, useDeleteRecordingMutation } from "../../hooks/useQueries";
import { useUIStore } from "../../stores/uiStore";
import { RecordingCard } from "./RecordingCard";
import { RecordingDeleteDialog } from "./RecordingDeleteDialog";
import { EmptyState } from "../ui/EmptyState";
import { LoadingState } from "../ui/LoadingState";
import { ErrorState } from "../ui/ErrorState";
import { useToast } from "../ui/ToastProvider";

type SortKey = "newest" | "oldest" | "longest" | "shortest";

const SORT_LABELS: Record<SortKey, string> = {
  newest: "Neueste",
  oldest: "Älteste",
  longest: "Längste",
  shortest: "Kürzeste",
};

export function RecordingList() {
  const query = useRecordingsQuery();
  const deleteMutation = useDeleteRecordingMutation();
  const confirmDelete = useUIStore((s) => s.confirmDelete);
  const toast = useToast();

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("newest");
  const [pendingDelete, setPendingDelete] = useState<Recording | null>(null);

  const recordings = useMemo(() => {
    const list = query.data?.recordings ?? [];
    const filtered = search.trim()
      ? list.filter((r) =>
          r.filename.toLowerCase().includes(search.trim().toLowerCase()),
        )
      : list;
    const sorted = [...filtered];
    switch (sort) {
      case "newest":
        sorted.sort((a, b) => b.created_at.localeCompare(a.created_at));
        break;
      case "oldest":
        sorted.sort((a, b) => a.created_at.localeCompare(b.created_at));
        break;
      case "longest":
        sorted.sort((a, b) => b.duration_seconds - a.duration_seconds);
        break;
      case "shortest":
        sorted.sort((a, b) => a.duration_seconds - b.duration_seconds);
        break;
    }
    return sorted;
  }, [query.data, search, sort]);

  const handleDeleteRequest = (recording: Recording) => {
    if (confirmDelete) {
      setPendingDelete(recording);
    } else {
      deleteMutation.mutate(recording.filename, {
        onSuccess: () => toast.show({ title: "Aufnahme gelöscht", variant: "success" }),
        onError: (err) =>
          toast.show({
            title: "Löschen fehlgeschlagen",
            description: err instanceof Error ? err.message : "Unbekannter Fehler",
            variant: "error",
          }),
      });
    }
  };

  const confirmDeleteAction = () => {
    if (!pendingDelete) return;
    deleteMutation.mutate(pendingDelete.filename, {
      onSuccess: () => {
        toast.show({ title: "Aufnahme gelöscht", variant: "success" });
        setPendingDelete(null);
      },
      onError: (err) => {
        toast.show({
          title: "Löschen fehlgeschlagen",
          description: err instanceof Error ? err.message : "Unbekannter Fehler",
          variant: "error",
        });
      },
    });
  };

  if (query.isLoading) {
    return <LoadingState message="Lade Aufnahmen …" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        title="Aufnahmen konnten nicht geladen werden"
        message={query.error instanceof Error ? query.error.message : "Unbekannter Fehler"}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if ((query.data?.recordings ?? []).length === 0) {
    return (
      <EmptyState
        title="Noch keine Aufnahmen vorhanden"
        description="Nimm im Voice Lab eine Sprachreferenz auf – sie erscheint automatisch hier."
      />
    );
  }

  return (
    <div className="page__section">
      <div className="list-controls">
        <div style={{ position: "relative", flex: 1, minWidth: 200 }}>
          <Search
            size={16}
            style={{
              position: "absolute",
              left: 10,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--color-text-muted)",
              pointerEvents: "none",
            }}
            aria-hidden="true"
          />
          <input
            className="list-controls__search"
            type="search"
            placeholder="Nach Dateiname suchen …"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Aufnahmen durchsuchen"
            style={{ paddingLeft: 32 }}
          />
        </div>
        <label htmlFor="recording-sort" className="sr-only">
          Sortierung
        </label>
        <select
          id="recording-sort"
          className="list-controls__select"
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="Aufnahmen sortieren"
        >
          {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
            <option key={key} value={key}>
              {SORT_LABELS[key]}
            </option>
          ))}
        </select>
      </div>

      {recordings.length === 0 ? (
        <EmptyState
          title="Keine Treffer"
          description="Für die Suche wurde keine Aufnahme gefunden."
        />
      ) : (
        <ul className="recording-list">
          {recordings.map((rec) => (
            <RecordingCard
              key={rec.filename}
              recording={rec}
              onDeleteRequest={handleDeleteRequest}
              deleting={deleteMutation.isPending}
            />
          ))}
        </ul>
      )}

      <RecordingDeleteDialog
        open={pendingDelete !== null}
        recording={pendingDelete}
        busy={deleteMutation.isPending}
        onConfirm={confirmDeleteAction}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
