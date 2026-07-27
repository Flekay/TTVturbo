import { useEffect, useState } from "react";
import { Plus, RefreshCw, Trash2, ExternalLink } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import {
  useCreateTwitchProfileMutation,
  useDeleteTwitchProfileMutation,
  useRefreshTwitchProfileMutation,
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
  const createMutation = useCreateTwitchProfileMutation();
  const refreshMutation = useRefreshTwitchProfileMutation();
  const deleteMutation = useDeleteTwitchProfileMutation();
  const syncMutation = useSyncVodsMutation();
  const activeProfileId = useActiveProfileStore((s) => s.activeProfileId);
  const setActiveProfile = useActiveProfileStore((s) => s.setActiveProfile);
  const clearActiveProfile = useActiveProfileStore((s) => s.clearActiveProfile);

  const [showCreate, setShowCreate] = useState(false);
  const [createValue, setCreateValue] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Validate the persisted active id against the loaded profiles. If the
  // id no longer matches a real profile (e.g. it was deleted on another
  // page), clear it in an effect rather than during render.
  const profiles = profilesQuery.data?.profiles ?? [];
  useEffect(() => {
    if (activeProfileId && profiles.length > 0 && !profiles.some((p) => p.id === activeProfileId)) {
      clearActiveProfile();
    }
  }, [activeProfileId, profiles, clearActiveProfile]);
  const activeProfile = profiles.find((p) => p.id === activeProfileId) ?? null;

  async function handleCreate() {
    setCreateError(null);
    const value = createValue.trim();
    if (!value) {
      setCreateError("Login oder Channel-URL darf nicht leer sein.");
      return;
    }
    try {
      const created = await createMutation.mutateAsync({
        login: value.includes("twitch.tv/") ? undefined : value,
        url: value.includes("twitch.tv/") ? value : undefined,
      });
      setActiveProfile(created.id);
      setShowCreate(false);
      setCreateValue("");
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Anlegen fehlgeschlagen.");
    }
  }

  function handleSelect(p: TwitchProfile) {
    setActiveProfile(p.id);
  }

  async function handleSync() {
    if (!activeProfile) return;
    try {
      await syncMutation.mutateAsync(activeProfile.id);
    } catch {
      // surfaced via toast / inline error in the VOD list
    }
  }

  async function handleRefresh() {
    if (!activeProfile) return;
    try {
      await refreshMutation.mutateAsync(activeProfile.id);
    } catch {
      // ignored - error surfaces via query state
    }
  }

  const pendingDelete = profiles.find((p) => p.id === confirmDeleteId) ?? null;

  return (
    <div className="vp-profile-selector">
      <div className="vp-profile-selector__header">
        <h3 className="vp-profile-selector__title">Twitch-Profile</h3>
        <div className="vp-profile-selector__actions">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowCreate(true)}
            aria-label="Profil hinzufügen"
          >
            <Plus size={14} /> Hinzufügen
          </Button>
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
            <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
              <Plus size={14} /> Profil hinzufügen
            </Button>
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

      {activeProfile && (
        <div className="vp-profile-selector__active-bar">
          <div className="vp-profile-selector__active-info">
            <span>Aktiv: <strong>{activeProfile.display_name}</strong></span>
            {activeProfile.last_synced_at && (
              <Badge variant="muted" title={activeProfile.last_synced_at}>
                zuletzt synchronisiert
              </Badge>
            )}
          </div>
          <div className="vp-profile-selector__active-actions">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleSync}
              loading={syncMutation.isPending}
              disabled={syncMutation.isPending}
            >
              <RefreshCw size={14} /> VODs synchronisieren
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              loading={refreshMutation.isPending}
              disabled={refreshMutation.isPending}
              aria-label="Profil aktualisieren"
            >
              <RefreshCw size={14} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmDeleteId(activeProfile.id)}
              aria-label="Profil löschen"
              disabled={deleteMutation.isPending}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        </div>
      )}

      {showCreate && (
        <div className="dialog-root" role="dialog" aria-modal="true">
          <div className="dialog-overlay" onClick={() => setShowCreate(false)} />
          <div className="dialog">
            <h3 className="dialog__title">Twitch-Profil hinzufügen</h3>
            <p className="dialog__description">
              Login (z. B. <code>flekay</code>) oder Channel-URL
              (z. B. <code>https://www.twitch.tv/flekay</code>).
            </p>
            <input
              className="input"
              type="text"
              value={createValue}
              onChange={(e) => setCreateValue(e.target.value)}
              placeholder="flekay oder https://www.twitch.tv/flekay"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
                if (e.key === "Escape") setShowCreate(false);
              }}
            />
            {createError && (
              <div className="vp-form-error" role="alert">
                {createError}
              </div>
            )}
            <div className="dialog__actions">
              <Button variant="secondary" onClick={() => setShowCreate(false)}>
                Abbrechen
              </Button>
              <Button
                variant="primary"
                onClick={handleCreate}
                loading={createMutation.isPending}
              >
                Hinzufügen
              </Button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(o) => {
          if (!o) setConfirmDeleteId(null);
        }}
        title="Profil löschen?"
        description={
          pendingDelete
            ? `Das Profil "${pendingDelete.display_name}" wird entfernt. ` +
              (typeof pendingDelete.vod_count === "number" && pendingDelete.vod_count > 0
                ? `Es hat ${pendingDelete.vod_count} VOD(s) angehängt – lösche diese zuerst.`
                : "Diese Aktion kann nicht rückgängig gemacht werden.")
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
            if (activeProfileId === pendingDelete.id) clearActiveProfile();
            setConfirmDeleteId(null);
          } catch (err) {
            // Conflict (VODs attached) is surfaced as a message via the
            // dialog description on next render; leave the dialog open.
            void err;
          }
        }}
      />
    </div>
  );
}
