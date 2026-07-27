import { Plus } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { LoadingState } from "../../components/ui/LoadingState";
import type { VoiceProfile } from "./types";

interface ProfileListProps {
  profiles: VoiceProfile[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewProfile: () => void;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "2-digit",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function ProfileList({
  profiles,
  selectedId,
  onSelect,
  onNewProfile,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ProfileListProps) {
  if (isLoading) {
    return <LoadingState message="Lade Profile …" />;
  }
  if (isError) {
    return (
      <ErrorState
        title="Profile konnten nicht geladen werden"
        message={errorMessage ?? "Unbekannter Fehler"}
        onRetry={onRetry}
      />
    );
  }
  if (profiles.length === 0) {
    return (
      <EmptyState
        title="Noch keine Voice-Profile"
        description="Erstelle dein erstes Profil, um Referenzaufnahmen zu verwalten."
        action={
          <Button variant="primary" size="sm" onClick={onNewProfile}>
            <Plus size={14} /> Neues Profil
          </Button>
        }
      />
    );
  }

  return (
    <div className="vp-profile-list" aria-label="Voice-Profile">
      <div className="vp-profile-list__header">
        <h3 className="vp-profile-list__title">Voice-Profile</h3>
        <Button
          variant="primary"
          size="sm"
          onClick={onNewProfile}
          className="vp-profile-list__new-btn"
        >
          <Plus size={14} /> Neues Profil
        </Button>
      </div>
      <div className="vp-profile-list__scroll">
        {profiles.map((profile) => {
          const active = profile.id === selectedId;
          return (
            <button
              key={profile.id}
              type="button"
              className={[
                "vp-profile-card",
                active ? "vp-profile-card--active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onSelect(profile.id)}
              aria-pressed={active}
              aria-label={`Profil ${profile.name} auswählen`}
            >
              <div className="vp-profile-card__name">{profile.name}</div>
              <div className="vp-profile-card__meta">
                <span>Erstellt: {formatDate(profile.created_at)}</span>
                <span>Akzeptiert: {profile.progress.accepted}</span>
                <span>Fortschritt: {Math.round(profile.progress.percentage ?? 0)}%</span>
              </div>
              <div className="vp-profile-card__badges">
                {profile.progress.clone_ready && (
                  <Badge variant="success">Clone-ready</Badge>
                )}
                {profile.progress.pack_complete && (
                  <Badge variant="success">Pack vollständig</Badge>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
