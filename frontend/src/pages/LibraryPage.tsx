import { useMemo, useRef, useState } from "react";
import { Download, Upload, Trash2, AlertCircle, Loader2, Film, FileVideo, Search, MoreVertical } from "lucide-react";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { Menu, MenuItem, MenuSeparator } from "../components/ui/Menu";
import { ApiError } from "../api/client";
import { formatBytes, formatDateTime, formatDuration, formatSpeed, formatEta } from "../utils/format";
import { useVodsQuery, useDeleteVodMutation } from "../features/vodPipeline/hooks";
import { vodFileUrl } from "../features/vodPipeline/api";
import type { TwitchVod } from "../features/vodPipeline/types";
import {
  useLibraryItemsQuery,
  useUploadToLibraryMutation,
  useDeleteLibraryItemMutation,
} from "../features/library/hooks";
import { libraryItemFileUrl } from "../features/library/api";
import type { LibraryItem as LibraryItemRecord } from "../features/library/schemas";

function isTransient(status: string): boolean {
  return status === "DOWNLOADING" || status === "QUEUED" || status === "VERIFYING";
}

/** Unified library item — normalises VODs and uploads into one shape. */
interface LibraryItem {
  id: string;
  kind: "vod" | "upload";
  title: string;
  subtitle: string;
  thumbnailUrl: string | null;
  durationLabel: string | null;
  fileUrl: string | null; // null while downloading
  fileSize: number | null;
  createdAt: string | null;
  status: "ready" | "downloading" | "queued" | "verifying" | "failed" | "canceled";
  percent: number | null;
  speedBps: number | null;
  etaSeconds: number | null;
  canDelete: boolean;
}

function vodToItem(vod: TwitchVod): LibraryItem {
  const transient = isTransient(vod.status);
  const ready = vod.status === "READY";
  return {
    id: vod.id,
    kind: "vod",
    title: vod.title || `VOD ${vod.twitch_video_id}`,
    subtitle: `#${vod.twitch_video_id}`,
    thumbnailUrl: vod.thumbnail_url || null,
    durationLabel: vod.duration_seconds != null ? formatDuration(vod.duration_seconds) : null,
    fileUrl: ready ? vodFileUrl(vod.id) : null,
    fileSize: vod.progress?.downloaded_bytes ?? null,
    createdAt: vod.published_at ?? null,
    status: ready ? "ready" : transient ? (vod.status.toLowerCase() as "downloading" | "queued" | "verifying") : (vod.status.toLowerCase() as "failed" | "canceled"),
    percent: vod.progress?.percent ?? null,
    speedBps: vod.progress?.speed_bytes_per_second ?? null,
    etaSeconds: vod.progress?.eta_seconds ?? null,
    canDelete: true,
  };
}

function libraryRecordToItem(rec: LibraryItemRecord): LibraryItem {
  return {
    id: rec.id,
    kind: rec.source === "vod" ? "vod" : "upload",
    title: rec.title,
    subtitle: rec.source === "vod" ? (rec.twitch_video_id ? `#${rec.twitch_video_id}` : "VOD") : "Upload",
    thumbnailUrl: null,
    durationLabel: rec.duration_seconds != null ? formatDuration(rec.duration_seconds) : null,
    fileUrl: libraryItemFileUrl(rec.id),
    fileSize: rec.file_size_bytes ?? null,
    createdAt: rec.created_at,
    status: "ready",
    percent: null,
    speedBps: null,
    etaSeconds: null,
    canDelete: true,
  };
}

type KindFilter = "all" | "vod" | "upload";
type StatusFilter = "all" | "ready" | "downloading";

const KIND_FILTER_LABELS: Record<KindFilter, string> = {
  all: "Alle Typen",
  vod: "VODs",
  upload: "Uploads",
};

const STATUS_FILTER_LABELS: Record<StatusFilter, string> = {
  all: "Alle Status",
  ready: "Bereit",
  downloading: "Wird geladen",
};

export function LibraryPage() {
  const vodsQuery = useVodsQuery({});
  const libraryQuery = useLibraryItemsQuery();
  const uploadMutation = useUploadToLibraryMutation();
  const deleteLibraryItemMutation = useDeleteLibraryItemMutation();
  const deleteVodMutation = useDeleteVodMutation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [confirmDelete, setConfirmDelete] = useState<LibraryItem | null>(null);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const allVods = vodsQuery.data?.vods ?? [];
  const libraryRecords = libraryQuery.data?.items ?? [];

  // VODs that are actively downloading (progress tracking from the VOD API).
  // READY VODs are represented by library items instead.
  const downloadingVods = useMemo(
    () => allVods.filter((v) => isTransient(v.status)),
    [allVods],
  );

  // Poll while any VOD is downloading.
  const anyDownloading = downloadingVods.some((v) => isTransient(v.status));
  const polledVodsQuery = useVodsQuery({}, { refetchInterval: anyDownloading ? 2000 : undefined });
  const effectiveDownloadingVods = anyDownloading
    ? (polledVodsQuery.data?.vods ?? allVods).filter((v) => isTransient(v.status))
    : downloadingVods;

  // Merge: downloading VODs (for progress) + library items (ready files).
  // Deduplicate: if a downloading VOD has a library_item_id, skip the
  // corresponding library item (the VOD card shows progress).
  const downloadingIds = new Set(effectiveDownloadingVods.map((v) => v.library_item_id).filter(Boolean));
  const readyLibraryItems = libraryRecords
    .filter((rec) => !downloadingIds.has(rec.id))
    .map(libraryRecordToItem);

  const items = useMemo(() => {
    const merged: LibraryItem[] = [
      ...effectiveDownloadingVods.map(vodToItem),
      ...readyLibraryItems,
    ];
    merged.sort((a, b) => {
      // Downloading items first.
      const aActive = a.status === "downloading" || a.status === "queued" || a.status === "verifying";
      const bActive = b.status === "downloading" || b.status === "queued" || b.status === "verifying";
      if (aActive && !bActive) return -1;
      if (!aActive && bActive) return 1;
      // Then by date descending.
      const aDate = a.createdAt ?? "";
      const bDate = b.createdAt ?? "";
      return bDate.localeCompare(aDate);
    });
    return merged;
  }, [effectiveDownloadingVods, readyLibraryItems]);

  // Apply search + filters.
  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      if (kindFilter !== "all" && item.kind !== kindFilter) return false;
      if (statusFilter === "ready" && item.status !== "ready") return false;
      if (statusFilter === "downloading" && !(item.status === "downloading" || item.status === "queued" || item.status === "verifying")) return false;
      if (q) {
        const hay = `${item.title} ${item.subtitle}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, search, kindFilter, statusFilter]);

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      void uploadMutation.mutateAsync(file);
      e.target.value = "";
    }
  };

  if (vodsQuery.isLoading || libraryQuery.isLoading) {
    return <LoadingState message="Bibliothek wird geladen …" />;
  }
  if (vodsQuery.isError) {
    return (
      <ErrorState
        title="Bibliothek konnte nicht geladen werden"
        message={vodsQuery.error instanceof ApiError ? vodsQuery.error.message : "Unbekannter Fehler"}
        onRetry={() => void vodsQuery.refetch()}
      />
    );
  }
  if (libraryQuery.isError) {
    return (
      <ErrorState
        title="Bibliothek konnte nicht geladen werden"
        message={libraryQuery.error instanceof ApiError ? libraryQuery.error.message : "Unbekannter Fehler"}
        onRetry={() => void libraryQuery.refetch()}
      />
    );
  }

  const readyCount = items.filter((i) => i.status === "ready").length;
  const downloadingCount = items.filter((i) => i.status === "downloading" || i.status === "queued" || i.status === "verifying").length;

  return (
    <div className="library-page">
      <div className="library-page__header">
        <p className="page__description">
          Alle Videos an einem Ort — heruntergeladene VODs und hochgeladene Dateien.
        </p>
        <div className="library-page__upload">
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,audio/*"
            onChange={handleFileSelected}
            style={{ display: "none" }}
            aria-label="Datei hochladen"
          />
          <Button
            variant="primary"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            loading={uploadMutation.isPending}
            disabled={uploadMutation.isPending}
          >
            <Upload size={14} /> Datei hochladen
          </Button>
        </div>
      </div>

      {uploadMutation.isError && (
        <div className="library-page__error" role="alert">
          <AlertCircle size={14} />
          <span>
            {uploadMutation.error instanceof ApiError
              ? uploadMutation.error.message
              : "Upload fehlgeschlagen."}
          </span>
        </div>
      )}

      {/* Search + filter controls */}
      <div className="list-controls">
        <div className="list-controls__search-wrap">
          <Search size={16} className="list-controls__search-icon" aria-hidden="true" />
          <input
            className="list-controls__search"
            type="search"
            placeholder="Bibliothek durchsuchen …"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Bibliothek durchsuchen"
          />
        </div>
        <label htmlFor="library-kind-filter" className="sr-only">
          Typ filtern
        </label>
        <select
          id="library-kind-filter"
          className="list-controls__select"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value as KindFilter)}
          aria-label="Typ filtern"
        >
          {(Object.keys(KIND_FILTER_LABELS) as KindFilter[]).map((k) => (
            <option key={k} value={k}>{KIND_FILTER_LABELS[k]}</option>
          ))}
        </select>
        <label htmlFor="library-status-filter" className="sr-only">
          Status filtern
        </label>
        <select
          id="library-status-filter"
          className="list-controls__select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          aria-label="Status filtern"
        >
          {(Object.keys(STATUS_FILTER_LABELS) as StatusFilter[]).map((s) => (
            <option key={s} value={s}>{STATUS_FILTER_LABELS[s]}</option>
          ))}
        </select>
      </div>

      <div className="library-page__counters">
        {downloadingCount > 0 && (
          <span className="library-page__counter library-page__counter--active">
            <Loader2 size={12} className="spin" /> {downloadingCount} wird geladen
          </span>
        )}
        <span className="library-page__counter">
          {readyCount} bereit
        </span>
        {filteredItems.length !== items.length && (
          <span className="library-page__counter">
            {filteredItems.length} von {items.length} angezeigt
          </span>
        )}
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Bibliothek ist leer"
          description="Lade VODs über den VOD Downloader herunter oder lade eine Datei hoch, um sie hier zu sehen."
        />
      ) : filteredItems.length === 0 ? (
        <EmptyState
          title="Keine Treffer"
          description="Keine Videos entsprechen den aktuellen Filtern."
        />
      ) : (
        <ul className="library-grid">
          {filteredItems.map((item) => (
            <LibraryCard
              key={`${item.kind}-${item.id}`}
              item={item}
              onDelete={() => setConfirmDelete(item)}
              deletePending={
                (deleteLibraryItemMutation.isPending && deleteLibraryItemMutation.variables === item.id) ||
                (deleteVodMutation.isPending && deleteVodMutation.variables === item.id)
              }
            />
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => { if (!o) setConfirmDelete(null); }}
        title="Aus Bibliothek löschen?"
        description={
          confirmDelete
            ? `"${confirmDelete.title}" wird dauerhaft aus der Bibliothek entfernt. Diese Aktion kann nicht rückgängig gemacht werden.`
            : ""
        }
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        busy={deleteLibraryItemMutation.isPending || deleteVodMutation.isPending}
        destructive
        onConfirm={async () => {
          if (!confirmDelete) return;
          try {
            if (confirmDelete.kind === "vod" && confirmDelete.status !== "ready") {
              // Downloading VOD: cancel + delete via VOD API.
              await deleteVodMutation.mutateAsync(confirmDelete.id);
            } else {
              // Ready library item (vod or upload): delete via library API.
              await deleteLibraryItemMutation.mutateAsync(confirmDelete.id);
            }
            setConfirmDelete(null);
          } catch {
            // leave dialog open on error
          }
        }}
      />
    </div>
  );
}

function LibraryCard({
  item,
  onDelete,
  deletePending,
}: {
  item: LibraryItem;
  onDelete: () => void;
  deletePending: boolean;
}) {
  const isDownloading = item.status === "downloading" || item.status === "queued" || item.status === "verifying";
  const pct = item.percent;
  const clipStyle = pct != null
    ? { clipPath: `inset(0 ${100 - Math.min(100, Math.max(0, pct))}% 0 0)` }
    : isDownloading
      ? { animation: "vp-thumb-indeterminate 1.4s ease-in-out infinite" }
      : undefined;

  const KindIcon = item.kind === "vod" ? Film : FileVideo;

  return (
    <li className="library-card">
      {/* Media area: video player when ready, progress thumbnail when downloading */}
      {isDownloading ? (
        item.thumbnailUrl ? (
          <div className="library-card__thumb library-card__thumb--progress">
            <img className="library-card__thumb-bw" src={item.thumbnailUrl} alt={item.title} loading="lazy" />
            <img className="library-card__thumb-color" src={item.thumbnailUrl} alt="" loading="lazy" style={clipStyle} />
            {pct != null && <span className="library-card__thumb-pct">{Math.round(pct)}%</span>}
            {item.durationLabel && <span className="library-card__duration">{item.durationLabel}</span>}
          </div>
        ) : (
          <div className="library-card__thumb library-card__thumb--placeholder">
            <Loader2 size={32} className="spin" />
          </div>
        )
      ) : item.fileUrl ? (
        <div className="library-card__video">
          <video
            src={item.fileUrl}
            poster={item.thumbnailUrl ?? undefined}
            controls
            preload="metadata"
            aria-label={item.title}
          />
        </div>
      ) : (
        <div className="library-card__thumb library-card__thumb--placeholder">
          <KindIcon size={32} />
        </div>
      )}

      <div className="library-card__body">
        <div className="library-card__title-row">
          <div className="library-card__title" title={item.title}>
            {item.title}
          </div>
          {/* 3-dots menu — right of the title */}
          <Menu
            trigger={
              <button
                type="button"
                className="btn btn--ghost btn--icon btn--sm library-card__menu-trigger"
                aria-label="Aktionen"
              >
                <MoreVertical size={16} />
              </button>
            }
          >
            {item.fileUrl && (
              <MenuItem onSelect={() => window.open(item.fileUrl!, "_blank")}>
                <Download size={14} /> Herunterladen
              </MenuItem>
            )}
            {item.fileUrl && <MenuSeparator />}
            {isDownloading && (
              <MenuItem disabled>
                <Loader2 size={14} className="spin" /> {pct != null ? `${Math.round(pct)}%` : "Lädt …"}
              </MenuItem>
            )}
            {item.canDelete && (
              <MenuItem onSelect={onDelete} disabled={deletePending} destructive>
                <Trash2 size={14} /> Löschen
              </MenuItem>
            )}
          </Menu>
        </div>
        <div className="library-card__meta">
          <span className="library-card__kind">
            <KindIcon size={11} /> {item.subtitle}
          </span>
          {item.fileSize != null && <span>{formatBytes(item.fileSize)}</span>}
          {item.createdAt && <span>{formatDateTime(item.createdAt)}</span>}
          {isDownloading && item.speedBps != null && item.speedBps > 0 && (
            <span>{formatSpeed(item.speedBps)}</span>
          )}
          {isDownloading && item.etaSeconds != null && item.etaSeconds > 0 && (
            <span>~{formatEta(item.etaSeconds)}</span>
          )}
        </div>
      </div>
    </li>
  );
}
