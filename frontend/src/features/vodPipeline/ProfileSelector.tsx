import { useEffect } from "react";
import { ExternalLink } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import {
  useSyncVodsMutation,
  useTwitchProfilesQuery,
} from "./hooks";
import { useActiveProfileStore } from "./activeProfileStore";
import type { TwitchProfile } from "./types";

function profileAvatarUrl(p: TwitchProfile): string | null {
  const url = p.avatar_url;
  if (url && url.trim()) return url.trim();
  return null;
}

interface ProfileSelectorProps {
  onOpenProfilesPage?: () => void;
}

export function ProfileSelector({ onOpenProfilesPage }: ProfileSelectorProps) {
  const profilesQuery = useTwitchProfilesQuery();
  const syncMutation = useSyncVodsMutation();
  const activeProfileId = useActiveProfileStore((s) => s.activeProfileId);
  const setActiveProfile = useActiveProfileStore((s) => s.setActiveProfile);
  const clearActiveProfile = useActiveProfileStore((s) => s.clearActiveProfile);

  // Validate the persisted active id against the loaded profiles. If the
  // id no longer matches a real profile (e.g. it was deleted on another
  // page), clear it in an effect rather than during render.
  const profiles = profilesQuery.data?.profiles ?? [];
  useEffect(() => {
    if (activeProfileId && profiles.length > 0 && !profiles.some((p) => p.id === activeProfileId)) {
      clearActiveProfile();
    }
  }, [activeProfileId, profiles, clearActiveProfile]);

  // Auto-sync VODs for the active profile whenever it changes (covers page
  // open / F5 / profile switch). The previous manual "VODs synchronisieren"
  // button was removed in favour of this automatic sync.
  useEffect(() => {
    if (!activeProfileId) return;
    void syncMutation.mutateAsync(activeProfileId).catch(() => {
      // Sync errors surface via the VOD list query state; ignore here.
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProfileId]);

  function handleSelect(p: TwitchProfile) {
    setActiveProfile(p.id);
  }

  return (
    <div className="vp-profile-selector">
      <div className="vp-profile-selector__header">
        <h3 className="vp-profile-selector__title">Twitch-Profile</h3>
        <div className="vp-profile-selector__actions">
          {onOpenProfilesPage && (
            <Button variant="ghost" size="sm" onClick={onOpenProfilesPage}>
              Verwalten <ExternalLink size={12} />
            </Button>
          )}
        </div>
      </div>

      {profilesQuery.isLoading ? (
        <LoadingState message="Lade Profile …" />
      ) : profilesQuery.isError ? (
        <ErrorState
          title="Profile konnten nicht geladen werden"
          message={
            profilesQuery.error instanceof ApiError
              ? profilesQuery.error.message
              : "Unbekannter Fehler"
          }
          onRetry={() => void profilesQuery.refetch()}
        />
      ) : profiles.length === 0 ? (
        <EmptyState
          title="Noch keine Twitch-Profile"
          description="Füge einen Twitch-Channel hinzu, um VODs zu synchronisieren."
          action={
            onOpenProfilesPage ? (
              <Button variant="primary" size="sm" onClick={onOpenProfilesPage}>
                Profil verwalten <ExternalLink size={12} />
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="vp-profile-selector__list">
          {profiles.map((p) => {
            const active = p.id === activeProfileId;
            const avatar = profileAvatarUrl(p);
            return (
              <button
                key={p.id}
                type="button"
                className={[
                  "vp-profile-card",
                  active ? "vp-profile-card--active" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => handleSelect(p)}
                aria-pressed={active}
              >
                <div className="vp-profile-card__avatar">
                  {avatar ? (
                    <img src={avatar} alt="" width={36} height={36} />
                  ) : (
                    <span aria-hidden="true">{p.display_name.slice(0, 1).toUpperCase()}</span>
                  )}
                </div>
                <div className="vp-profile-card__body">
                  <div className="vp-profile-card__name">{p.display_name}</div>
                  <div className="vp-profile-card__meta">
                    <span>@{p.login}</span>
                    {typeof p.vod_count === "number" && (
                      <span>{p.vod_count} VODs</span>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

    </div>
  );
}
