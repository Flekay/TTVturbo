import { useState } from "react";
import { Link2, Plus } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../api/client";
import { useImportVodMutation } from "./hooks";

interface ImportVodPanelProps {
  profileId: string | null;
}

/**
 * Manual VOD / clip link import.
 *
 * Accepts ``twitch.tv/videos/<id>`` and ``twitch.tv/<channel>/clip/<slug>``
 * URLs. The backend re-validates and rejects channel URLs / foreign
 * domains with a 400. The profile must be selected first.
 */
export function ImportVodPanel({ profileId }: ImportVodPanelProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const importMutation = useImportVodMutation();

  async function handleImport() {
    setError(null);
    setSuccess(null);
    if (!profileId) {
      setError("Wähle zuerst ein Twitch-Profil aus.");
      return;
    }
    const value = url.trim();
    if (!value) {
      setError("VOD-URL darf nicht leer sein.");
      return;
    }
    try {
      const vod = await importMutation.mutateAsync({ profile_id: profileId, url: value });
      setSuccess(`VOD "${vod.title || vod.twitch_video_id}" wurde importiert.`);
      setUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import fehlgeschlagen.");
    }
  }

  return (
    <div className="vp-import-panel">
      <div className="vp-import-panel__header">
        <Link2 size={16} />
        <h3 className="vp-import-panel__title">VOD-Link importieren</h3>
      </div>
      <p className="vp-import-panel__hint">
        VOD- oder Clip-URLs von <code>twitch.tv</code>. Der VOD muss zum
        ausgewählten Profil gehören.
      </p>
      <div className="vp-import-panel__row">
        <input
          className="input"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.twitch.tv/videos/1234567890 oder …/casepayt/clip/Slug"
          disabled={!profileId || importMutation.isPending}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleImport();
          }}
        />
        <Button
          variant="primary"
          size="sm"
          onClick={handleImport}
          loading={importMutation.isPending}
          disabled={!profileId || importMutation.isPending}
        >
          <Plus size={14} /> Importieren
        </Button>
      </div>
      {error && (
        <div className="vp-form-error" role="alert">
          {error}
        </div>
      )}
      {success && (
        <div className="vp-form-success" role="status">
          {success}
        </div>
      )}
    </div>
  );
}
