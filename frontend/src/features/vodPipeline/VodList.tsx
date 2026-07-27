import { useMemo, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { Download, Search, Loader2, AlertCircle, X, Check, HardDrive, ExternalLink } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { Tooltip } from "../../components/ui/Tooltip";
import { ApiError } from "../../api/client";
import { formatBytes, formatDateTime, formatDuration, formatSpeed, formatEta } from "../../utils/format";
import {
  useStartDownloadMutation,
  useCancelDownloadMutation,
  useVodsQuery,
} from "./hooks";
import { vodStreamDownloadUrl, vodFileUrl } from "./api";
import { KNOWN_VOD_STATUSES } from "./schemas";
import type { TwitchVod } from "./types";

interface VodListProps {
  profileId: string | null;
}

type VodTypeFilter = "all" | "vod" | "clip";

const VOD_TYPE_TABS: { id: VodTypeFilter; label: string }[] = [
  { id: "all", label: "Alle" },
  { id: "vod", label: "VODs" },
  { id: "clip", label: "Clips" },
];

export function VodList({ profileId }: VodListProps) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest" | "longest" | "shortest">("newest");
  const [typeFilter, setTypeFilter] = useState<VodTypeFilter>("all");

  const params = useMemo(
    () => ({ profile_id: profileId ?? undefined, search: search || undefined, sort }),
    [profileId, search, sort],
  );
  // First fetch to detect transient states (library download running).
  const vodsQuery = useVodsQuery(params);
  const vods = vodsQuery.data?.vods ?? [];
  const anyTransient = vods.some(
    (v) => v.status === "DOWNLOADING" || v.status === "QUEUED" || v.status === "VERIFYING",
  );
  // Re-subscribe with polling when a library download is running.
  const polledQuery = useVodsQuery(params, { refetchInterval: anyTransient ? 2000 : undefined });
  const effectiveQuery = anyTransient ? polledQuery : vodsQuery;
  const effectiveVods = effectiveQuery.data?.vods ?? [];
  // Client-side type filter (backend has no type param). The backend uses
  // "archive" for VODs and "clip" for clips; the "vod" tab maps to "archive".
  const visibleVods = useMemo(() => {
    if (typeFilter === "all") return effectiveVods;
    const backendType = typeFilter === "vod" ? "archive" : "clip";
    return effectiveVods.filter((v) => v.type === backendType);
  }, [effectiveVods, typeFilter]);

  const startMutation = useStartDownloadMutation();
  const cancelMutation = useCancelDownloadMutation();

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

  return (
    <div className="vp-vod-list">
      <div className="vp-vod-list__tabs" role="tablist">
        {VOD_TYPE_TABS.map((tab) => {
          const active = tab.id === typeFilter;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              className={[
                "vp-vod-list__tab",
                active ? "vp-vod-list__tab--active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => setTypeFilter(tab.id)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
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

      {visibleVods.length === 0 ? (
        <EmptyState
          title={typeFilter === "clip" ? "Keine Clips" : "Keine VODs"}
          description={
            typeFilter === "clip"
              ? "Für dieses Profil wurden keine Clips synchronisiert."
              : "Für dieses Profil wurden keine VODs synchronisiert."
          }
        />
      ) : (
        <ul className="vp-vod-list__items">
          {visibleVods.map((vod) => (
            <VodRow
              key={vod.id}
              vod={vod}
              onLibraryDownload={() => void startMutation.mutateAsync(vod.id)}
              onLibraryCancel={() => void cancelMutation.mutateAsync(vod.id)}
              libraryPending={
                (startMutation.isPending && startMutation.variables === vod.id) ||
                (cancelMutation.isPending && cancelMutation.variables === vod.id)
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface VodRowProps {
  vod: TwitchVod;
  onLibraryDownload: () => void;
  onLibraryCancel: () => void;
  libraryPending: boolean;
}

type StreamState =
  | { status: "idle" }
  | { status: "preparing" }
  | { status: "downloading"; bytesReceived: number; speedBps: number }
  | { status: "done" }
  | { status: "error"; message: string };

function isLibraryTransient(status: string): boolean {
  return status === "DOWNLOADING" || status === "QUEUED" || status === "VERIFYING";
}

/** Fixed-width status pill — icon only, no text changes → no layout shift. */
function StatusPill({ status }: { status: string }) {
  const transient = isLibraryTransient(status);
  const ready = status === "READY";
  const failed = status === "FAILED" || status === "CANCELED";
  return (
    <span
      className="vp-vod-row__status-pill"
      data-state={ready ? "ready" : transient ? "transient" : failed ? "failed" : "idle"}
      title={
        ready ? "Geladen" :
        status === "DOWNLOADING" ? "Wird geladen…" :
        status === "QUEUED" ? "In Warteschlange" :
        status === "VERIFYING" ? "Wird verifiziert…" :
        status === "FAILED" ? "Fehlgeschlagen" :
        status === "CANCELED" ? "Abgebrochen" :
        "Nicht geladen"
      }
    >
      {ready ? <Check size={14} /> : transient ? <Loader2 size={14} className="spin" /> : <HardDrive size={14} />}
    </span>
  );
}

/**
 * Progress thumbnail: two images stacked — bottom grayscale, top in color.
 * The color layer is clipped via `clip-path: inset()` to reveal only
 * `percent` of the width, creating a "fill with color" effect.
 */
function ProgressThumbnail({
  src,
  alt,
  percent,
  durationLabel,
}: {
  src: string;
  alt: string;
  percent: number | null;
  durationLabel?: string;
}) {
  const pct = percent != null ? Math.min(100, Math.max(0, percent)) : null;
  const clipStyle = pct != null
    ? { clipPath: `inset(0 ${100 - pct}% 0 0)` }
    : { clipPath: "inset(0 0 0 0)", animation: "vp-thumb-indeterminate 1.4s ease-in-out infinite" };
  return (
    <div className="vp-vod-row__thumb vp-vod-row__thumb--progress">
      <img
        className="vp-vod-row__thumb-bw"
        src={src}
        alt={alt}
        loading="lazy"
        onError={(e) => { (e.currentTarget as HTMLImageElement).parentElement!.style.display = "none"; }}
      />
      <img
        className="vp-vod-row__thumb-color"
        src={src}
        alt=""
        loading="lazy"
        style={clipStyle}
      />
      {durationLabel && <span className="vp-vod-row__duration">{durationLabel}</span>}
      {pct != null && (
        <span className="vp-vod-row__thumb-pct">{Math.round(pct)}%</span>
      )}
    </div>
  );
}

/** Compact stats line (speed, bytes, eta) — shown below the thumbnail. */
function ProgressStats({
  bytesReceived,
  totalBytes,
  speedBps,
  etaSeconds,
}: {
  bytesReceived: number | null;
  totalBytes: number | null;
  speedBps: number | null;
  etaSeconds: number | null;
}) {
  const hasStats =
    bytesReceived != null || (speedBps != null && speedBps > 0) || (etaSeconds != null && etaSeconds > 0);
  if (!hasStats) return null;
  return (
    <div className="vp-vod-row__progress-stats">
      {bytesReceived != null && (
        <span className="vp-vod-row__progress-bytes">
          {formatBytes(bytesReceived)}{totalBytes != null ? ` / ${formatBytes(totalBytes)}` : ""}
        </span>
      )}
      {speedBps != null && speedBps > 0 && (
        <span className="vp-vod-row__progress-speed">{formatSpeed(speedBps)}</span>
      )}
      {etaSeconds != null && etaSeconds > 0 && (
        <span className="vp-vod-row__progress-eta">~{formatEta(etaSeconds)}</span>
      )}
    </div>
  );
}

function VodRow({
  vod,
  onLibraryDownload,
  onLibraryCancel,
  libraryPending,
}: VodRowProps) {
  const [stream, setStream] = useState<StreamState>({ status: "idle" });
  const abortRef = useRef<AbortController | null>(null);

  const libStatus = vod.status;
  const libReady = libStatus === "READY";
  const libTransient = isLibraryTransient(libStatus);
  const streamBusy = stream.status === "preparing" || stream.status === "downloading";
  const streamDownloading = stream.status === "downloading";

  // Determine the active progress percent for the thumbnail.
  // Library download takes priority, then browser stream.
  const activePercent = libTransient
    ? (vod.progress?.percent ?? null)
    : streamDownloading
      ? null  // browser stream has no percent (no Content-Length from yt-dlp)
      : null;
  const showProgressThumb = (libTransient || streamDownloading) && !!vod.thumbnail_url;

  /** Browser download: use cached file when READY, stream from yt-dlp otherwise. */
  const handleBrowserDownload = async () => {
    if (streamBusy) return;
    // READY → direct link to cached file, no fetch needed.
    if (libReady) {
      const a = document.createElement("a");
      a.href = vodFileUrl(vod.id);
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
      return;
    }
    // Not ready → stream from yt-dlp via fetch with progress.
    setStream({ status: "preparing" });
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fetch(vodStreamDownloadUrl(vod.id), { signal: controller.signal });
      if (!res.ok) {
        let message = `Download fehlgeschlagen (HTTP ${res.status})`;
        try {
          const body = await res.json();
          if (body?.detail?.message) message = body.detail.message;
        } catch { /* ignore */ }
        setStream({ status: "error", message });
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        setStream({ status: "error", message: "Stream konnte nicht gelesen werden." });
        return;
      }
      const chunks: BlobPart[] = [];
      let received = 0;
      const startTime = performance.now();
      let lastUpdate = startTime;
      let lastReceived = 0;
      // Exponential moving average for smoother speed display.
      let emaSpeed = 0;
      setStream({ status: "downloading", bytesReceived: 0, speedBps: 0 });
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) {
          chunks.push(value as BlobPart);
          received += value.length;
          const now = performance.now();
          // Update speed at most every 250ms to avoid jitter.
          if (now - lastUpdate >= 250) {
            const dt = (now - lastUpdate) / 1000;
            const instantSpeed = (received - lastReceived) / dt;
            emaSpeed = emaSpeed === 0 ? instantSpeed : emaSpeed * 0.7 + instantSpeed * 0.3;
            lastUpdate = now;
            lastReceived = received;
            setStream({ status: "downloading", bytesReceived: received, speedBps: emaSpeed });
          }
        }
      }
      const blob = new Blob(chunks, { type: res.headers.get("Content-Type") || "video/mp4" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const disp = res.headers.get("Content-Disposition") || "";
      const m = /filename="([^"]+)"/.exec(disp);
      a.download = m?.[1] || `${vod.title || vod.id}.mp4`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStream({ status: "done" });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setStream({ status: "idle" });
      } else {
        setStream({
          status: "error",
          message: err instanceof Error ? err.message : "Download fehlgeschlagen.",
        });
      }
    } finally {
      abortRef.current = null;
    }
  };

  const handleCancelStream = () => abortRef.current?.abort();

  return (
    <li className="vp-vod-row">
      {showProgressThumb ? (
        <ProgressThumbnail
          src={vod.thumbnail_url!}
          alt={vod.title || `VOD ${vod.twitch_video_id}`}
          percent={activePercent}
          durationLabel={vod.duration_seconds != null ? formatDuration(vod.duration_seconds) : undefined}
        />
      ) : vod.thumbnail_url ? (
        <div className="vp-vod-row__thumb">
          <img
            src={vod.thumbnail_url}
            alt={vod.title || `VOD ${vod.twitch_video_id}`}
            loading="lazy"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
          />
          {vod.duration_seconds != null && (
            <span className="vp-vod-row__duration">{formatDuration(vod.duration_seconds)}</span>
          )}
        </div>
      ) : null}

      <div className="vp-vod-row__body">
        <div className="vp-vod-row__title-block">
          <div className="vp-vod-row__title" title={vod.title || vod.twitch_video_id}>
            {vod.title || `VOD ${vod.twitch_video_id}`}
            {vod.type === "clip" && (
              <Badge variant="muted" title="Clip">Clip</Badge>
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
            {vod.published_at && <span>{formatDateTime(vod.published_at)}</span>}
          </div>
        </div>
        <StatusPill status={libStatus} />
      </div>

      {/* Stats line — shown while any download is active. Fixed height prevents shift. */}
      <div className="vp-vod-row__progress-slot">
        {libTransient && (
          <ProgressStats
            bytesReceived={vod.progress?.downloaded_bytes ?? null}
            totalBytes={vod.progress?.total_bytes ?? null}
            speedBps={vod.progress?.speed_bytes_per_second ?? null}
            etaSeconds={vod.progress?.eta_seconds ?? null}
          />
        )}
        {!libTransient && streamDownloading && (
          <ProgressStats
            bytesReceived={stream.bytesReceived}
            totalBytes={null}
            speedBps={stream.speedBps}
            etaSeconds={null}
          />
        )}
      </div>

      {stream.status === "error" && (
        <div className="vp-vod-row__dl-error" role="alert">
          <AlertCircle size={14} />
          <span>{stream.message}</span>
        </div>
      )}

      <div className="vp-vod-row__actions">
        {/* Primary: Laden (download to server library) */}
        {libTransient ? (
          <Button
            variant="primary"
            size="sm"
            onClick={onLibraryCancel}
            loading={libraryPending}
            disabled={libraryPending}
            aria-label="Download abbrechen"
          >
            <X size={14} /> Abbrechen
          </Button>
        ) : libReady ? (
          <Tooltip content="In Bibliothek ansehen" side="top">
            <Link
              to="/library"
              className="btn btn--primary btn--sm"
              aria-label="In Bibliothek ansehen"
            >
              <ExternalLink size={14} /> In Bibliothek
            </Link>
          </Tooltip>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={onLibraryDownload}
            loading={libraryPending}
            disabled={libraryPending || streamBusy}
            aria-label="Auf Server laden"
          >
            <HardDrive size={14} /> Auf Server laden
          </Button>
        )}

        {/* Secondary: Herunterladen (browser download — cached if READY, stream otherwise) */}
        {stream.status === "preparing" && (
          <Tooltip content="Wird vorbereitet …" side="top">
            <Button variant="ghost" size="sm" disabled aria-label="Wird vorbereitet">
              <Loader2 size={14} className="spin" />
            </Button>
          </Tooltip>
        )}
        {stream.status === "downloading" && (
          <Tooltip content={`${formatBytes(stream.bytesReceived)} — klick zum Abbrechen`} side="top">
            <Button variant="ghost" size="sm" onClick={handleCancelStream} aria-label="Download abbrechen">
              <Loader2 size={14} className="spin" />
            </Button>
          </Tooltip>
        )}
        {(stream.status === "idle" || stream.status === "done" || stream.status === "error") && (
          <Tooltip content="Herunterladen" side="top">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleBrowserDownload}
              disabled={streamBusy || libraryPending}
              aria-label="Herunterladen"
            >
              <Download size={14} />
            </Button>
          </Tooltip>
        )}
      </div>
    </li>
  );
}

// Re-export for tests / consumers that want the known statuses.
export { KNOWN_VOD_STATUSES };
