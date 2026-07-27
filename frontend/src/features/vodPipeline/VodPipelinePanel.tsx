import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ProfileSelector } from "./ProfileSelector";
import { ImportVodPanel } from "./ImportVodPanel";
import { VodList } from "./VodList";
import { useActiveProfileStore } from "./activeProfileStore";
import { useSyncVodsMutation, useTwitchProfilesQuery } from "./hooks";

/**
 * Top-level VOD Pipeline panel.
 *
 * Two distinct workflows, visually separated by a divider:
 *  1. VOD-Link import (top, full width) — paste a twitch.tv URL.
 *  2. Profile-based browse/sync/download (below) — profile selector +
 *     VOD list with download controls.
 * All server state is TanStack Query; only the active profile id is
 * persisted across reloads (via zustand/persist in activeProfileStore).
 *
 * The auto-sync lives here (not in ProfileSelector) so the sync status
 * is visible to both the selector and the VOD list.
 */
export function VodPipelinePanel() {
  const navigate = useNavigate();
  const activeProfileId = useActiveProfileStore((s) => s.activeProfileId);
  const setActiveProfile = useActiveProfileStore((s) => s.setActiveProfile);
  const clearActiveProfile = useActiveProfileStore((s) => s.clearActiveProfile);
  const profilesQuery = useTwitchProfilesQuery();
  const syncMutation = useSyncVodsMutation();

  const profiles = profilesQuery.data?.profiles ?? [];

  // Auto-select the first profile when none is selected and profiles have
  // loaded. Without this, a fresh user (or after deleting the previously
  // selected profile) sees "Kein Profil ausgewählt" and the auto-sync
  // never fires.
  useEffect(() => {
    if (activeProfileId === null && profiles.length > 0) {
      setActiveProfile(profiles[0].id);
    }
  }, [activeProfileId, profiles, setActiveProfile]);

  // Drop a persisted selection that no longer matches a real profile.
  useEffect(() => {
    if (activeProfileId && profiles.length > 0 && !profiles.some((p) => p.id === activeProfileId)) {
      clearActiveProfile();
    }
  }, [activeProfileId, profiles, clearActiveProfile]);

  // Track which profiles have completed a sync (success OR failure). This
  // prevents VodList from showing "Keine VODs" before the first sync has
  // had a chance to populate the list.
  const [syncedIds, setSyncedIds] = useState<Set<string>>(new Set());

  // Auto-sync VODs for the active profile whenever it changes (covers page
  // open / F5 / profile switch). On completion (success or error) the
  // profile id is recorded so VodList knows the sync is done.
  useEffect(() => {
    if (!activeProfileId) return;
    let cancelled = false;
    syncMutation.mutateAsync(activeProfileId)
      .catch(() => {
        // Errors are recorded below; the profile is still marked as
        // synced so the user sees the error state, not infinite loading.
      })
      .finally(() => {
        if (!cancelled) {
          setSyncedIds((prev) =>
            prev.has(activeProfileId) ? prev : new Set(prev).add(activeProfileId),
          );
        }
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProfileId]);

  const syncing = syncMutation.isPending && syncMutation.variables === activeProfileId;
  const hasSynced = activeProfileId !== null && syncedIds.has(activeProfileId);
  const syncError = !syncing && syncMutation.isError && syncMutation.variables === activeProfileId
    ? (syncMutation.error instanceof Error ? syncMutation.error.message : "Sync fehlgeschlagen.")
    : null;

  return (
    <div className="vp-vod-pipeline">
      <ImportVodPanel profileId={activeProfileId} />
      <hr className="vp-vod-pipeline__divider" />
      <div className="vp-vod-pipeline__layout">
        <ProfileSelector onOpenProfilesPage={() => navigate("/twitch-profiles")} />
        <div className="vp-vod-pipeline__main">
          <VodList
            profileId={activeProfileId}
            syncing={syncing}
            syncError={syncError}
            hasSynced={hasSynced}
          />
        </div>
      </div>
    </div>
  );
}
