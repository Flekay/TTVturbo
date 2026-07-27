import { useMemo, useState } from "react";
import {
  Download,
  Play,
  RefreshCw,
  Trash2,
  X,
  AlertCircle,
  FileVideo,
  Search,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import { formatBytes, formatDateTime, formatDuration } from "../../utils/format";
import {
  useCancelDownloadMutation,
  useDeleteVodMutation,
  useRetryDownloadMutation,
  useStartDownloadMutation,
  useVodsQuery,
} from "./hooks";
import { vodFileUrl } from "./api";
import { KNOWN_VOD_STATUSES, type KnownVodStatus } from "./schemas";
import type { TwitchVod } from "./types";

function statusBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status as KnownVodStatus) {
    case "READY":
      return { variant: "success", label: "Bereit" };
    case "DOWNLOADING":
      return { variant: "info", label: "Lädt" };
    case "VERIFYING":
      return { variant: "info", label: "Verifiziert" };
    case "QUEUED":
      return { variant: "info", label: "Wartet" };
    case "FAILED":
      return { variant: "error", label: "Fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Abgebrochen" };
    case "DISCOVERED":
      return { variant: "muted", label: "Entdeckt" };
    default:
      return { variant: "muted", label: status };
  }
}

function isTransient(status: string): boolean {
  return status === "DOWNLOADING" || status === "QUEUED" || status === "VERIFYING";
}

function isStartable(status: string): boolean {
  return status === "DISCOVERED" || status === "FAILED" || status === "CANCELED";
}

function isCancellable(status: string): boolean {
  return status === "DOWNLOADING" || status === "QUEUED" || status === "VERIFYING";
}

function isRetryable(status: string): boolean {
  return status === "FAILED" || status === "CANCELED";
}

interface VodListProps {
  profileId: string | null;
}

export function VodList({ profileId }: VodListProps) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest" | "longest" | "shortest">("newest");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Poll while any VOD is in a transient state so progress updates live.
  const params = useMemo(
    () => ({ profile_id: profileId ?? undefined, search: search || undefined, sort }),
    [profileId, search, sort],
  );
  // First fetch to detect transient states.
  const vodsQuery = useVodsQuery(params);
  const vods = vodsQuery.data?.vods ?? [];
  const anyTransient = vods.some((v) => isTransient(v.status));
  // Re-subscribe with polling enabled when needed. We use a second query
  // with refetchInterval so we don't poll when nothing is running.
  const polledQuery = useVodsQuery(params, { refetchInterval: anyTransient ? 2000 : undefined });
  const effectiveQuery = anyTransient ? polledQuery : vodsQuery;
  const effectiveVods = effectiveQuery.data?.vods ?? [];

  const startMutation = useStartDownloadMutation();
  const cancelMutation = useCancelDownloadMutation();
  const retryMutation = useRetryDownloadMutation();
  const deleteMutation = useDeleteVodMutation();

  if (!profileId) {
    return (
      <EmptyState
        title="Kein Profil ausgewählt"
        description="Wähle oben ein Twitch-Profil aus, um dessen VODs anzuzeigen."
      />
    );
  }
  if (effectiveQuery.isLoading) {
    return <LoadingState message="Lade VODs …" />;
  }
  if (effectiveQuery.isError) {
    return (
      <ErrorState
        title="VODs konnten nicht geladen werden"
        message={
          effectiveQuery.error instanceof ApiError
            ? effectiveQuery.error.message
            : "Unbekannter Fehler"
        }
        onRetry={() => void effectiveQuery.refetch()}
      />
    );
  }
  if (effectiveVods.length === 0) {
    return (
      <EmptyState
        title="Keine VODs"
        description="Synchronisiere VODs für dieses Profil oder importiere einen VOD-Link manuell."
      />
    );
  }

  const pendingDelete = effectiveVods.find((v) => v.id === confirmDeleteId) ?? null;

  return (
    <div className="vp-vod-list">
      <div className="vp-vod-list__toolbar">
        <div className="vp-vod-list__search">
          <Search size={14} />
          <input
            className="input"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="VODs durchsuchen …"
            aria-label="VODs durchsuchen"
          />
        </div>
        <select
          className="input"
          value={sort}
          onChange={(e) => setSort(e.target.value as typeof sort)}
          aria-label="Sortierung"
        >
          <option value="newest">Neueste zuerst</option>
          <option value="oldest">Älteste zuerst</option>
          <option value="longest">Längste zuerst</option>
          <option value="shortest">Kürzeste zuerst</option>
        </select>
      </div>

      <ul className="vp-vod-list__items">
        {effectiveVods.map((vod) => (
          <VodRow
            key={vod.id}
            vod={vod}
            onStart={() => void startMutation.mutateAsync(vod.id)}
            onCancel={() => void cancelMutation.mutateAsync(vod.id)}
            onRetry={() => void retryMutation.mutateAsync(vod.id)}
            onDelete={() => setConfirmDeleteId(vod.id)}
            startPending={startMutation.isPending && startMutation.variables === vod.id}
            cancelPending={cancelMutation.isPending && cancelMutation.variables === vod.id}
            retryPending={retryMutation.isPending && retryMutation.variables === vod.id}
            deletePending={deleteMutation.isPending && deleteMutation.variables === vod.id}
          />
        ))}
      </ul>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(o) => {
          if (!o) setConfirmDeleteId(null);
        }}
        title="VOD löschen?"
        description={
          pendingDelete
            ? `Der VOD "${pendingDelete.title || pendingDelete.twitch_video_id}" wird inkl. heruntergeladener Videodatei entfernt. Diese Aktion kann nicht rückgängig gemacht werden.`
            : ""
        }
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        busy={deleteMutation.isPending}
        destructive
        onConfirm={async () => {
          if (!pendingDelete) return;
          try {
            await deleteMutation.mutateAsync(pendingDelete.id);
            setConfirmDeleteId(null);
          } catch {
            // leave dialog open on error
          }
        }}
      />
    </div>
  );
}

interface VodRowProps {
  vod: TwitchVod;
  onStart: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
  startPending: boolean;
  cancelPending: boolean;
  retryPending: boolean;
  deletePending: boolean;
}

function VodRow({
  vod,
  onStart,
  onCancel,
  onRetry,
  onDelete,
  startPending,
  cancelPending,
  retryPending,
  deletePending,
}: VodRowProps) {
  const badge = statusBadge(vod.status);
  const transient = isTransient(vod.status);
  const startable = isStartable(vod.status);
  const cancellable = isCancellable(vod.status);
  const retryable = isRetryable(vod.status);
  const ready = vod.status === "READY";
  const progress = vod.progress;
  const percent =
    progress?.percent != null ? Math.max(0, Math.min(100, progress.percent)) : null;

  return (
    <li className="vp-vod-row">
      <div className="vp-vod-row__head">
        <div className="vp-vod-row__title-block">
          <div className="vp-vod-row__title" title={vod.title || vod.twitch_video_id}>
            {vod.title || `VOD ${vod.twitch_video_id}`}
            {vod.type === "clip" && (
              <Badge variant="muted" title="Clip">
                Clip
              </Badge>
            )}
          </div>
          <div className="vp-vod-row__meta">
            <a
              href={vod.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="vp-vod-row__link"
            >
              #{vod.twitch_video_id}
            </a>
            {vod.duration_seconds != null && (
              <span>{formatDuration(vod.duration_seconds)}</span>
            )}
            {vod.published_at && <span>{formatDateTime(vod.published_at)}</span>}
          </div>
        </div>
        <Badge variant={badge.variant} title={vod.status}>
          {badge.label}
        </Badge>
      </div>

      {transient && (
        <div className="vp-vod-row__progress">
          <div className="vp-vod-row__progress-bar">
            <div
              className="vp-vod-row__progress-fill"
              style={{ width: `${percent ?? 0}%` }}
            />
          </div>
          <div className="vp-vod-row__progress-text">
            {percent != null ? `${percent.toFixed(1)}%` : "Läuft …"}
            {progress?.speed_bytes_per_second != null && (
              <span> · {formatBytes(progress.speed_bytes_per_second)}/s</span>
            )}
            {progress?.eta_seconds != null && (
              <span> · ETA {formatDuration(progress.eta_seconds)}</span>
            )}
          </div>
        </div>
      )}

      {vod.status === "FAILED" && vod.error && (
        <div className="vp-vod-row__error" role="alert">
          <AlertCircle size={14} />
          <span>{vod.error}</span>
        </div>
      )}
      {vod.status === "CANCELED" && vod.error && (
        <div className="vp-vod-row__error vp-vod-row__error--muted">
          <AlertCircle size={14} />
          <span>{vod.error}</span>
        </div>
      )}

      {ready && vod.download.file_name && (
        <div className="vp-vod-row__ready">
          <FileVideo size={14} />
          <span>
            {vod.download.file_name} · {formatBytes(vod.download.file_size_bytes ?? 0)}
            {vod.download.width && vod.download.height
              ? ` · ${vod.download.width}×${vod.download.height}`
              : ""}
            {vod.download.video_codec ? ` · ${vod.download.video_codec}` : ""}
          </span>
        </div>
      )}

      <div className="vp-vod-row__actions">
        {startable && (
          <Button
            variant="primary"
            size="sm"
            onClick={onStart}
            loading={startPending}
            disabled={startPending}
          >
            <Download size={14} /> Herunterladen
          </Button>
        )}
        {cancellable && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onCancel}
            loading={cancelPending}
            disabled={cancelPending}
          >
            <X size={14} /> Abbrechen
          </Button>
        )}
        {retryable && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onRetry}
            loading={retryPending}
            disabled={retryPending}
          >
            <RefreshCw size={14} /> Erneut versuchen
          </Button>
        )}
        {ready && vod.download.file_name && (
          <a
            className="btn btn--primary btn--sm"
            href={vodFileUrl(vod.id)}
            download
            aria-label="Videodatei herunterladen"
          >
            <Play size={14} /> Datei
          </a>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          loading={deletePending}
          disabled={deletePending}
          aria-label="VOD löschen"
        >
          <Trash2 size={14} />
        </Button>
      </div>
    </li>
  );
}

// Re-export for tests / consumers that want the known statuses.
export { KNOWN_VOD_STATUSES };
