import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Play,
  Search,
  RefreshCw,
  AlertCircle,
  Check,
  Link2,
  ExternalLink,
} from "lucide-react";
import { Badge, type BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import { formatDateTime, formatDuration } from "../../utils/format";
import {
  useTwitchProfilesQuery,
  useVodsQuery,
  useSyncVodsMutation,
} from "./hooks";
import {
  useStartVodPipelineRunBatchMutation,
  useStartVodPipelineRunMutation,
  usePipelineRunsFilteredQuery,
} from "../mediaProcessing";
import type { TwitchVod, TwitchProfile } from "./types";
import type {
  PipelineRunBatchResponse,
  PipelineSourceContract,
} from "../mediaProcessing";

const ACTIVE_STATUSES = "QUEUED,RUNNING,WAITING_FOR_GPU,CANCELING,RETRYING";

interface VodPipelineStartPanelProps {
  /** Called after a successful start so the page can switch to the Aktiv tab. */
  onStarted?: () => void;
}

type Sort = "newest" | "oldest" | "longest" | "shortest";

const SORT_OPTIONS: { value: Sort; label: string }[] = [
  { value: "newest", label: "Neueste zuerst" },
  { value: "oldest", label: "Älteste zuerst" },
  { value: "longest", label: "Dauer aufsteigend" },
  { value: "shortest", label: "Dauer absteigend" },
];

function libraryStatusBadge(vod: TwitchVod): { variant: BadgeVariant; label: string } {
  switch (vod.status) {
    case "READY":
      return { variant: "success", label: "In Library" };
    case "DOWNLOADING":
      return { variant: "info", label: "Download läuft" };
    case "QUEUED":
      return { variant: "muted", label: "In Warteschlange" };
    case "VERIFYING":
      return { variant: "info", label: "Wird verifiziert" };
    case "FAILED":
      return { variant: "error", label: "Download fehlgeschlagen" };
    case "CANCELED":
      return { variant: "muted", label: "Download abgebrochen" };
    default:
      return { variant: "muted", label: "Nicht in Library" };
  }
}

function pipelineStatusBadge(
  vod: TwitchVod,
  activeByExtId: Map<string, boolean>,
  completedByExtId: Map<string, boolean>,
  failedByExtId: Map<string, boolean>,
): { variant: BadgeVariant; label: string } | null {
  const extId = vod.twitch_video_id;
  if (activeByExtId.get(extId)) {
    return { variant: "info", label: "Pipeline aktiv" };
  }
  if (failedByExtId.get(extId)) {
    return { variant: "error", label: "Pipeline fehlgeschlagen" };
  }
  if (completedByExtId.get(extId)) {
    return { variant: "success", label: "Pipeline abgeschlossen" };
  }
  return { variant: "muted", label: "Nicht verarbeitet" };
}

function vodToSource(vod: TwitchVod): PipelineSourceContract {
  return {
    provider: "twitch",
    source_type: vod.type === "clip" ? "clip" : "vod",
    external_id: vod.twitch_video_id,
    url: vod.source_url,
  };
}

/**
 * VOD Pipeline start panel.
 *
 * Two start paths sharing the same backend orchestration:
 *  1. (primary) select one or more VODs from known Twitch profiles and start
 *     a pipeline run per selected VOD via the batch endpoint;
 *  2. (secondary, compact) paste a direct Twitch VOD / clip URL.
 *
 * Reuses the existing VOD-downloader data hooks (profiles, VODs, sync) and
 * the media-processing pipeline-run hooks. No second Twitch sync or parallel
 * import logic is built here.
 */
export function VodPipelineStartPanel({ onStarted }: VodPipelineStartPanelProps) {
  const [profileFilter, setProfileFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>("newest");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [syncError, setSyncError] = useState<string | null>(null);
  const [batchResult, setBatchResult] = useState<PipelineRunBatchResponse | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  // URL import state.
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);

  const profilesQuery = useTwitchProfilesQuery();
  const profiles = profilesQuery.data?.profiles ?? [];

  const vodsParams = useMemo(
    () => ({
      profile_id: profileFilter === "all" ? undefined : profileFilter,
      search: search || undefined,
      sort,
    }),
    [profileFilter, search, sort],
  );
  const vodsQuery = useVodsQuery(vodsParams);
  const vods = vodsQuery.data?.vods ?? [];

  // Active runs (to disable checkboxes + show "Pipeline aktiv") and a
  // limited history (to show completed / failed status). Both are mapped by
  // stable Twitch external id, not by URL.
  const activeRunsQuery = usePipelineRunsFilteredQuery(
    { status: ACTIVE_STATUSES },
    { refetchInterval: 3000 },
  );
  const historyRunsQuery = usePipelineRunsFilteredQuery(
    { status: "COMPLETED,FAILED,CANCELED", limit: 200 },
    { refetchInterval: 10_000 },
  );

  const activeByExtId = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const run of activeRunsQuery.data?.pipeline_runs ?? []) {
      const extId = run.source?.external_id;
      if (extId) m.set(extId, true);
    }
    return m;
  }, [activeRunsQuery.data]);

  const completedByExtId = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const run of historyRunsQuery.data?.pipeline_runs ?? []) {
      if (run.status === "COMPLETED" && run.source?.external_id) {
        m.set(run.source.external_id, true);
      }
    }
    return m;
  }, [historyRunsQuery.data]);

  const failedByExtId = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const run of historyRunsQuery.data?.pipeline_runs ?? []) {
      if (run.status === "FAILED" && run.source?.external_id) {
        m.set(run.source.external_id, true);
      }
    }
    return m;
  }, [historyRunsQuery.data]);

  const profileById = useMemo(() => {
    const m = new Map<string, TwitchProfile>();
    for (const p of profiles) m.set(p.id, p);
    return m;
  }, [profiles]);

  const syncMutation = useSyncVodsMutation();
  const batchMutation = useStartVodPipelineRunBatchMutation();
  const urlMutation = useStartVodPipelineRunMutation();

  const syncing = syncMutation.isPending;

  async function handleSync() {
    setSyncError(null);
    try {
      if (profileFilter === "all") {
        for (const p of profiles) {
          await syncMutation.mutateAsync(p.id);
        }
      } else {
        await syncMutation.mutateAsync(profileFilter);
      }
    } catch (err) {
      setSyncError(err instanceof ApiError ? err.message : "Sync fehlgeschlagen.");
    }
  }

  function toggleVod(vodId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(vodId)) next.delete(vodId);
      else next.add(vodId);
      return next;
    });
  }

  const visibleVods = vods;
  const allVisibleSelected =
    visibleVods.length > 0 && visibleVods.every((v) => selected.has(v.id) || activeByExtId.get(v.twitch_video_id));
  const selectableVisible = visibleVods.filter((v) => !activeByExtId.get(v.twitch_video_id));
  const selectedCount = selected.size;

  function selectAllVisible() {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const v of selectableVisible) next.add(v.id);
      return next;
    });
  }

  function clearSelection() {
    setSelected(new Set());
  }

  // Drop selection for VODs no longer in the loaded dataset (e.g. after a
  // search/filter change). Selection is preserved while a VOD is still part
  // of the current result set.
  const visibleIds = useMemo(() => new Set(visibleVods.map((v) => v.id)), [visibleVods]);
  const effectiveSelected = useMemo(() => {
    const dropped: string[] = [];
    for (const id of selected) {
      if (!visibleIds.has(id)) dropped.push(id);
    }
    if (dropped.length === 0) return selected;
    const next = new Set(selected);
    for (const id of dropped) next.delete(id);
    return next;
  }, [selected, visibleIds]);

  async function handleBatchStart() {
    setBatchError(null);
    setBatchResult(null);
    const selectedVods = visibleVods.filter((v) => effectiveSelected.has(v.id));
    if (selectedVods.length === 0) return;
    // Reprocessing guard: require explicit confirmation when any selected
    // VOD already has a completed pipeline run.
    const anyCompleted = selectedVods.some((v) => completedByExtId.get(v.twitch_video_id));
    if (anyCompleted) {
      const ok = window.confirm(
        "Mindestens ein ausgewähltes VOD wurde bereits vollständig verarbeitet. Erneut verarbeiten?",
      );
      if (!ok) return;
    }
    const sources = selectedVods.map(vodToSource);
    try {
      const result = await batchMutation.mutateAsync({ sources });
      setBatchResult(result);
      // Clear only successfully started selections.
      const startedIds = new Set(
        result.created
          .map((c) => c.source_external_id)
          .filter((id): id is string => !!id),
      );
      setSelected((prev) => {
        const next = new Set(prev);
        for (const v of selectedVods) {
          if (startedIds.has(v.twitch_video_id)) next.delete(v.id);
        }
        return next;
      });
      if (result.created.length > 0) {
        onStarted?.();
      }
    } catch (err) {
      setBatchError(err instanceof ApiError ? err.message : "Batch-Start fehlgeschlagen.");
    }
  }

  function handleUrlStart() {
    setUrlError(null);
    const trimmed = url.trim();
    if (!trimmed) {
      setUrlError("Bitte eine Twitch-VOD- oder Clip-URL eingeben.");
      return;
    }
    urlMutation.mutate(
      { url: trimmed },
      {
        onSuccess: () => {
          setUrl("");
          onStarted?.();
        },
        onError: (err) => {
          setUrlError(err instanceof ApiError ? err.message : "Pipeline konnte nicht gestartet werden.");
        },
      },
    );
  }

  const profilesLoading = profilesQuery.isLoading;
  const vodsLoading = vodsQuery.isLoading;

  return (
    <div className="vp-start-panel">
      {/* Secondary: direct URL quick-import (compact) */}
      <Card className="vp-start-panel__url-card">
        <div className="vp-start-panel__url-head">
          <Link2 size={14} />
          <span className="vp-start-panel__url-title">Direkte URL</span>
        </div>
        <div className="vp-start-panel__url-row">
          <input
            id="vp-url-input"
            className="input vp-start-panel__url-input"
            type="url"
            placeholder="https://www.twitch.tv/videos/123456789"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !urlMutation.isPending) handleUrlStart();
            }}
            disabled={urlMutation.isPending}
            aria-invalid={!!urlError}
            aria-label="Twitch-VOD- oder Clip-URL"
          />
          <Button
            variant="primary"
            size="sm"
            onClick={handleUrlStart}
            disabled={urlMutation.isPending || !url.trim()}
            loading={urlMutation.isPending}
          >
            <Play size={14} /> Pipeline starten
          </Button>
        </div>
        <span className="vp-start-panel__url-hint">
          Beliebige Twitch-VOD- oder Clip-URL einfügen. Profil und Metadaten werden automatisch erkannt.
        </span>
        {urlError && (
          <div className="vp-form-error" role="alert">
            <AlertCircle size={14} />
            <span>{urlError}</span>
          </div>
        )}
      </Card>

      {/* Primary: profile filter + sync + VOD selection */}
      <Card className="vp-start-panel__select-card">
        <div className="vp-start-panel__filter-row">
          <div className="vp-start-panel__field">
            <label className="vp-start-panel__label" htmlFor="vp-profile-filter">
              Twitch-Profil
            </label>
            <select
              id="vp-profile-filter"
              className="input"
              value={profileFilter}
              onChange={(e) => setProfileFilter(e.target.value)}
              disabled={profilesLoading || profiles.length === 0}
              aria-label="Twitch-Profil filtern"
            >
              <option value="all">Alle Profile</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSync}
            disabled={syncing || profiles.length === 0}
            loading={syncing}
          >
            <RefreshCw size={14} /> VODs synchronisieren
          </Button>
        </div>
        {syncError && (
          <div className="vp-form-error" role="alert">
            <AlertCircle size={14} />
            <span>{syncError}</span>
          </div>
        )}

        {/* Toolbar: search + sort + select-all */}
        {profiles.length > 0 && (
          <div className="vp-start-panel__toolbar">
            <div className="vp-vod-list__search">
              <Search size={14} />
              <input
                className="input"
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Titel, Profil oder Twitch-ID durchsuchen …"
                aria-label="VODs durchsuchen"
              />
            </div>
            <select
              className="input"
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              aria-label="Sortierung"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="sm"
              onClick={allVisibleSelected ? clearSelection : selectAllVisible}
              disabled={selectableVisible.length === 0}
            >
              {allVisibleSelected ? "Auswahl aufheben" : "Alle sichtbaren auswählen"}
            </Button>
          </div>
        )}

        {/* VOD list / states */}
        {profilesLoading ? (
          <LoadingState message="Lade Profile …" />
        ) : profilesQuery.isError ? (
          <ErrorState
            title="Profile konnten nicht geladen werden"
            message={profilesQuery.error instanceof ApiError ? profilesQuery.error.message : "Unbekannter Fehler"}
            onRetry={() => void profilesQuery.refetch()}
          />
        ) : profiles.length === 0 ? (
          <EmptyState
            title="Keine Twitch-Profile vorhanden."
            description='Füge unter „Twitch-Profile" ein Profil hinzu oder starte eine Pipeline direkt über eine VOD-URL.'
            action={
              <Link className="btn btn--primary btn--sm" to="/twitch-profiles">
                Twitch-Profile verwalten <ExternalLink size={12} />
              </Link>
            }
          />
        ) : vodsLoading ? (
          <LoadingState message="Lade VODs …" />
        ) : vodsQuery.isError ? (
          <ErrorState
            title="VODs konnten nicht geladen werden"
            message={vodsQuery.error instanceof ApiError ? vodsQuery.error.message : "Unbekannter Fehler"}
            onRetry={() => void vodsQuery.refetch()}
          />
        ) : visibleVods.length === 0 ? (
          <EmptyState
            title="Keine VODs gefunden."
            description="Synchronisiere die ausgewählten Twitch-Profile."
          />
        ) : (
          <ul className="vp-vod-sel-list" data-testid="vp-vod-sel-list">
            {visibleVods.map((vod) => {
              const libBadge = libraryStatusBadge(vod);
              const pipeBadge = pipelineStatusBadge(
                vod,
                activeByExtId,
                completedByExtId,
                failedByExtId,
              );
              const isActive = activeByExtId.get(vod.twitch_video_id) === true;
              const checked = effectiveSelected.has(vod.id);
              const profile = vod.profile_id ? profileById.get(vod.profile_id) : undefined;
              return (
                <li
                  key={vod.id}
                  className="vp-vod-sel-row"
                  data-vod-id={vod.id}
                  data-active={isActive ? "true" : "false"}
                >
                  <input
                    type="checkbox"
                    className="vp-vod-sel-row__check"
                    checked={checked}
                    disabled={isActive}
                    onChange={() => toggleVod(vod.id)}
                    aria-label={`VOD ${vod.title || vod.twitch_video_id} auswählen`}
                  />
                  {vod.thumbnail_url ? (
                    <div className="vp-vod-sel-row__thumb">
                      <img
                        src={vod.thumbnail_url}
                        alt={vod.title || `VOD ${vod.twitch_video_id}`}
                        loading="lazy"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = "none";
                        }}
                      />
                    </div>
                  ) : (
                    <div className="vp-vod-sel-row__thumb vp-vod-sel-row__thumb--placeholder">
                      {vod.type === "clip" ? "Clip" : "VOD"}
                    </div>
                  )}
                  <div className="vp-vod-sel-row__body">
                    <div className="vp-vod-sel-row__title" title={vod.title || vod.twitch_video_id}>
                      {vod.title || `VOD ${vod.twitch_video_id}`}
                    </div>
                    <div className="vp-vod-sel-row__meta">
                      <a
                        href={vod.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="vp-vod-row__link"
                      >
                        #{vod.twitch_video_id}
                      </a>
                      {profile && <span>{profile.display_name}</span>}
                      {vod.published_at && <span>{formatDateTime(vod.published_at)}</span>}
                      {vod.duration_seconds != null && (
                        <span>{formatDuration(vod.duration_seconds)}</span>
                      )}
                    </div>
                  </div>
                  <div className="vp-vod-sel-row__badges">
                    <Badge variant={libBadge.variant} title={libBadge.label}>{libBadge.label}</Badge>
                    {pipeBadge && (
                      <Badge variant={pipeBadge.variant} title={pipeBadge.label}>{pipeBadge.label}</Badge>
                    )}
                    {isActive && (
                      <Link
                        className="btn btn--ghost btn--sm"
                        to="/vod-pipeline?tab=history"
                        aria-label="Aktiven Run anzeigen"
                      >
                        Aktiven Run anzeigen
                      </Link>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {/* Selection action bar */}
        {profiles.length > 0 && visibleVods.length > 0 && (
          <div className="vp-start-panel__selection-bar">
            <span className="vp-start-panel__selection-count">
              {selectedCount > 0
                ? selectedCount === 1
                  ? "1 VOD ausgewählt"
                  : `${selectedCount} VODs ausgewählt`
                : ""}
            </span>
            <Button
              variant="primary"
              size="sm"
              onClick={handleBatchStart}
              disabled={selectedCount === 0 || batchMutation.isPending}
              loading={batchMutation.isPending}
            >
              <Play size={14} />
              {selectedCount === 1
                ? "VOD zur Pipeline hinzufügen"
                : `${selectedCount} ausgewählte VODs zur Pipeline hinzufügen`}
            </Button>
          </div>
        )}

        {/* Batch result summary (partial success / conflicts / failures) */}
        {batchResult && (
          <div className="vp-start-panel__batch-result" role="status">
            {batchResult.created.length > 0 && (
              <div className="vp-start-panel__batch-line vp-start-panel__batch-line--ok">
                <Check size={14} /> {batchResult.created.length} Run(s) gestartet.
              </div>
            )}
            {batchResult.conflicts.length > 0 && (
              <div className="vp-start-panel__batch-line vp-start-panel__batch-line--warn">
                <AlertCircle size={14} />
                <span>
                  {batchResult.conflicts.length} Konflikt(e):{" "}
                  {batchResult.conflicts.map((c, i) => (
                    <span key={i}>
                      {c.source_external_id ?? "?"} ({c.code}){i < batchResult.conflicts.length - 1 ? ", " : ""}
                    </span>
                  ))}
                </span>
              </div>
            )}
            {batchResult.failed.length > 0 && (
              <div className="vp-start-panel__batch-line vp-start-panel__batch-line--err">
                <AlertCircle size={14} />
                <span>
                  {batchResult.failed.length} fehlgeschlagen:{" "}
                  {batchResult.failed.map((f, i) => (
                    <span key={i}>
                      {f.source_external_id ?? "?"} ({f.message}){i < batchResult.failed.length - 1 ? ", " : ""}
                    </span>
                  ))}
                </span>
              </div>
            )}
          </div>
        )}
        {batchError && (
          <div className="vp-form-error" role="alert">
            <AlertCircle size={14} />
            <span>{batchError}</span>
          </div>
        )}
      </Card>
    </div>
  );
}

// Re-export for convenience.
export { ACTIVE_STATUSES };
