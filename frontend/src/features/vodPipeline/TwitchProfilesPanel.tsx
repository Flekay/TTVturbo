import { useState } from "react";
import { ExternalLink, Plus, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import { Tooltip } from "../../components/ui/Tooltip";
import { ApiError } from "../../api/client";
import { formatDateTime } from "../../utils/format";
import {
  useCreateTwitchProfileMutation,
  useDeleteTwitchProfileMutation,
  useTwitchProfilesQuery,
} from "./hooks";
import type { TwitchProfile } from "./types";

function profileAvatarUrl(p: TwitchProfile): string | null {
  const url = p.avatar_url;
  if (url && url.trim()) return url.trim();
  return null;
}

/**
 * Full Twitch Profiles management panel (separate page).
 *
 * Shows every profile with full metadata and a delete action.
 * Sync and refresh happen automatically — no manual buttons needed.
 */
export function TwitchProfilesPanel() {
  const profilesQuery = useTwitchProfilesQuery();
  const createMutation = useCreateTwitchProfileMutation();
  const deleteMutation = useDeleteTwitchProfileMutation();

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
              onDelete={() => setConfirmDeleteId(p.id)}
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
            ? `Das Profil "${pendingDelete.display_name}" wird entfernt. Diese Aktion kann nicht rückgängig gemacht werden.`
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
  onDelete: () => void;
  deletePending: boolean;
}

function ProfileRow({
  profile,
  onDelete,
  deletePending,
}: ProfileRowProps) {
  const avatar = profileAvatarUrl(profile);
  return (
    <li className="vp-profiles-page__row">
      <div className="vp-profiles-page__avatar">
        {avatar ? (
          <img src={avatar} alt="" width={48} height={48} />
        ) : (
          <span aria-hidden="true">
            {profile.display_name.slice(0, 1).toUpperCase()}
          </span>
        )}
      </div>
      <div className="vp-profiles-page__body">
        <div className="vp-profiles-page__name">
          {profile.display_name}
          <Tooltip content="Twitch-Channel öffnen" side="top">
            <a
              href={`https://www.twitch.tv/${profile.login}`}
              target="_blank"
              rel="noopener noreferrer"
              className="vp-profiles-page__ext"
              aria-label="Twitch-Channel öffnen"
            >
              <ExternalLink size={12} />
            </a>
          </Tooltip>
        </div>
        <div className="vp-profiles-page__meta">
          <span>@{profile.login}</span>
        </div>
        <div className="vp-profiles-page__dates">
          <span>Erstellt: {formatDateTime(profile.created_at)}</span>
          {profile.last_synced_at && (
            <span>Sync: {formatDateTime(profile.last_synced_at)}</span>
          )}
        </div>
      </div>
      <div className="vp-profiles-page__actions">
        <Tooltip content="Profil löschen" side="top">
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
        </Tooltip>
      </div>
    </li>
  );
}
