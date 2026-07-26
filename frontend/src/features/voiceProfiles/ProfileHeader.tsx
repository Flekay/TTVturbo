import { Pencil, Trash2 } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import type { VoiceProfile } from "./types";

interface ProfileHeaderProps {
  profile: VoiceProfile;
  onRename: () => void;
  onDelete: () => void;
  busy: boolean;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function ProfileHeader({
  profile,
  onRename,
  onDelete,
  busy,
}: ProfileHeaderProps) {
  return (
    <header className="vp-profile-header">
      <div className="vp-profile-header__row">
        <div className="vp-profile-header__title">{profile.name}</div>
        <div className="vp-profile-actions">
          <Button variant="secondary" size="sm" onClick={onRename} disabled={busy}>
            <Pencil size={14} /> Umbenennen
          </Button>
          <Button variant="danger" size="sm" onClick={onDelete} disabled={busy}>
            <Trash2 size={14} /> Löschen
          </Button>
        </div>
      </div>
      <div className="vp-profile-header__sub">
        <span>Locale: {profile.locale}</span>
        <span>Erstellt: {formatDate(profile.created_at)}</span>
        <span>Akzeptierte Referenzen: {profile.progress.accepted}</span>
        {profile.progress.clone_ready && <Badge variant="success">Clone-ready</Badge>}
        {profile.progress.pack_complete && <Badge variant="success">Pack vollständig</Badge>}
      </div>
    </header>
  );
}
