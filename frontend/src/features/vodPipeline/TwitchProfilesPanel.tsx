import { useState } from "react";
import { ExternalLink, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { ApiError } from "../../api/client";
import { formatDateTime } from "../../utils/format";
import {
  useCreateTwitchProfileMutation,
  useDeleteTwitchProfileMutation,
  useRefreshTwitchProfileMutation,
  useSyncVodsMutation,
  useTwitchProfilesQuery,
} from "./hooks";
import type { TwitchProfile } from "./types";

/**
 * Full Twitch Profiles management panel (separate page).
 *
 * Shows every profile with full metadata, VOD counts and per-profile
 * actions (sync, refresh, delete). Mirrors the structure of the Voice
 * Profiles management page.
 */
export function TwitchProfilesPanel() {
  const profilesQuery = useTwitchProfilesQuery();
  const createMutation = useCreateTwitchProfileMutation();
  const refreshMutation = useRefreshTwitchProfileMutation();
  const deleteMutation = useDeleteTwitchProfileMutation();
  const syncMutation = useSyncVodsMutation();

  const [showCreate, setShowCreate] = useState(false);
  const [createValue, setCreateValue] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const profiles = profilesQuery.data?.profiles ?? [];
  const pendingDelete = profiles.find((p) => p.id === confirmDeleteId) ?? null;

  async function handleCreate() {
    setCreateError(null);
    const value = createValue.trim();
    if (!value) {
      setCreateError("Login oder Channel-URL darf nicht leer sein.");
      return;
    }
    try {
      await createMutation.mutateAsync({
        login: value.includes("twitch.tv/") ? undefined : value,
        url: value.includes("twitch.tv/") ? value : undefined,
      });
      setShowCreate(false);
      setCreateValue("");
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Anlegen fehlgeschlagen.");
    }
  }

  return (
    <div className="vp-profiles-page">
      <div className="vp-profiles-page__header">
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
          <Plus size={14} /> Profil hinzufügen
        </Button>
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
          description="Füge einen Twitch-Channel hinzu, um VODs zu synchronisieren und herunterzuladen."
          action={
            <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
              <Plus size={14} /> Profil hinzufügen
            </Button>
          }
        />
      ) : (
        <ul className="vp-profiles-page__list">
          {profiles.map((p) => (
            <ProfileRow
              key={p.id}
              profile={p}
              onSync={() => void syncMutation.mutateAsync(p.id)}
              onRefresh={() => void refreshMutation.mutateAsync(p.id)}
              onDelete={() => setConfirmDeleteId(p.id)}
              syncPending={syncMutation.isPending && syncMutation.variables === p.id}
              refreshPending={refreshMutation.isPending && refreshMutation.variables === p.id}
              deletePending={deleteMutation.isPending && deleteMutation.variables === p.id}
            />
          ))}
        </ul>
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
            setConfirmDeleteId(null);
          } catch {
            // leave open
          }
        }}
      />
    </div>
  );
}

interface ProfileRowProps {
  profile: TwitchProfile;
  onSync: () => void;
  onRefresh: () => void;
  onDelete: () => void;
  syncPending: boolean;
  refreshPending: boolean;
  deletePending: boolean;
}

function ProfileRow({
  profile,
  onSync,
  onRefresh,
  onDelete,
  syncPending,
  refreshPending,
  deletePending,
}: ProfileRowProps) {
  return (
    <li className="vp-profiles-page__row">
      <div className="vp-profiles-page__avatar">
        <span aria-hidden="true">
          {profile.display_name.slice(0, 1).toUpperCase()}
        </span>
      </div>
      <div className="vp-profiles-page__body">
        <div className="vp-profiles-page__name">
          {profile.display_name}
          <a
            href={`https://www.twitch.tv/${profile.login}`}
            target="_blank"
            rel="noopener noreferrer"
            className="vp-profiles-page__ext"
            aria-label="Twitch-Channel öffnen"
          >
            <ExternalLink size={12} />
          </a>
        </div>
        <div className="vp-profiles-page__meta">
          <span>@{profile.login}</span>
          {typeof profile.vod_count === "number" && (
            <span>{profile.vod_count} VODs</span>
          )}
        </div>
        <div className="vp-profiles-page__dates">
          <span>Erstellt: {formatDateTime(profile.created_at)}</span>
          {profile.last_synced_at && (
            <span>Sync: {formatDateTime(profile.last_synced_at)}</span>
          )}
        </div>
      </div>
      <div className="vp-profiles-page__actions">
        <Button
          variant="secondary"
          size="sm"
          onClick={onSync}
          loading={syncPending}
          disabled={syncPending}
        >
          <RefreshCw size={14} /> Sync
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          loading={refreshPending}
          disabled={refreshPending}
          aria-label="Profil aktualisieren"
        >
          <RefreshCw size={14} />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          loading={deletePending}
          disabled={deletePending}
          aria-label="Profil löschen"
        >
          <Trash2 size={14} />
        </Button>
      </div>
    </li>
  );
}
