import { useState } from "react";
import { Link2, HardDrive, Download, Loader2, AlertCircle } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../api/client";
import { useImportVodMutation, useStartDownloadMutation } from "./hooks";
import { vodStreamDownloadUrl } from "./api";
import type { TwitchVod } from "./types";

interface ImportVodPanelProps {
  profileId?: string | null;
}

/**
 * Manual VOD / clip link import.
 *
 * Accepts ``twitch.tv/videos/<id>`` and ``twitch.tv/<channel>/clip/<slug>``
 * URLs. The backend re-validates and rejects channel URLs / foreign
 * domains with a 400. A profile must be selected first.
 *
 * Two actions (mirroring the VOD list rows below):
 *  - "Auf Server laden": import + start a server-side library download.
 *  - "Direkter Download": import + stream the video directly to the
 *    browser via yt-dlp without persisting on the server.
 */
export function ImportVodPanel({ profileId }: ImportVodPanelProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [serverPending, setServerPending] = useState(false);
  const [streamStatus, setStreamStatus] = useState<
    "idle" | "preparing" | "downloading" | "done"
  >("idle");
  const importMutation = useImportVodMutation();
  const startMutation = useStartDownloadMutation();

  async function importIfNeeded(): Promise<TwitchVod | null> {
    const value = url.trim();
    if (!value) {
      setError("VOD-URL darf nicht leer sein.");
      return null;
    }
    setError(null);
    try {
      return await importMutation.mutateAsync({
        profile_id: profileId ?? null,
        url: value,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import fehlgeschlagen.");
      return null;
    }
  }

  async function handleServerDownload() {
    if (serverPending || streamStatus === "preparing" || streamStatus === "downloading") return;
    setServerPending(true);
    setError(null);
    try {
      const vod = await importIfNeeded();
      if (!vod) return;
      await startMutation.mutateAsync(vod.id);
      setUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Server-Download fehlgeschlagen.");
    } finally {
      setServerPending(false);
    }
  }

  async function handleDirectDownload() {
    if (serverPending || streamStatus === "preparing" || streamStatus === "downloading") return;
    setStreamStatus("preparing");
    setError(null);
    try {
      const vod = await importIfNeeded();
      if (!vod) {
        setStreamStatus("idle");
        return;
      }
      const res = await fetch(vodStreamDownloadUrl(vod.id));
      if (!res.ok) {
        let message = `Download fehlgeschlagen (HTTP ${res.status})`;
        try {
          const body = await res.json();
          if (body?.detail?.message) message = body.detail.message;
        } catch { /* ignore */ }
        setStreamStatus("idle");
        setError(message);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        setStreamStatus("idle");
        setError("Stream konnte nicht gelesen werden.");
        return;
      }
      const chunks: BlobPart[] = [];
      setStreamStatus("downloading");
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) chunks.push(value as BlobPart);
      }
      const blob = new Blob(chunks, { type: res.headers.get("Content-Type") || "video/mp4" });
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      const disp = res.headers.get("Content-Disposition") || "";
      const m = /filename="([^"]+)"/.exec(disp);
      a.download = m?.[1] || `${vod.title || vod.id}.mp4`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
      setStreamStatus("done");
      setUrl("");
    } catch (err) {
      setStreamStatus("idle");
      setError(err instanceof Error ? err.message : "Download fehlgeschlagen.");
    }
  }

  const busy = serverPending || streamStatus === "preparing" || streamStatus === "downloading";

  return (
    <div className="vp-import-panel">
      <div className="vp-import-panel__header">
        <Link2 size={16} />
        <h3 className="vp-import-panel__title">VOD-Link importieren</h3>
      </div>
      <p className="vp-import-panel__hint">
        VOD- oder Clip-URLs von <code>twitch.tv</code>.
      </p>
      <div className="vp-import-panel__row">
        <input
          className="input"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.twitch.tv/videos/1234567890 oder …/casepayt/clip/Slug"
          disabled={busy}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleServerDownload();
          }}
        />
        <Button
          variant="primary"
          size="sm"
          onClick={handleServerDownload}
          loading={serverPending}
          disabled={busy}
        >
          <HardDrive size={14} /> Auf Server laden
        </Button>
        {streamStatus === "preparing" || streamStatus === "downloading" ? (
          <Button variant="ghost" size="sm" disabled aria-label="Wird herunterladen">
            <Loader2 size={14} className="spin" />
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDirectDownload}
            disabled={busy}
            aria-label="Direkter Download"
          >
            <Download size={14} />
          </Button>
        )}
      </div>
      {error && (
        <div className="vp-form-error" role="alert">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
