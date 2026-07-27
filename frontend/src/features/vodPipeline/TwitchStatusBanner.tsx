import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { Badge } from "../../components/ui/Badge";
import { useTwitchStatusQuery } from "./hooks";

/**
 * Twitch runtime status banner.
 *
 * Shows a compact, color-coded summary of the VOD pipeline tooling health
 * (yt-dlp, ffprobe, download directory). No Twitch API credentials are
 * needed — yt-dlp handles everything. When everything is healthy the
 * banner collapses to a single positive line so it does not dominate
 * the page.
 */
export function TwitchStatusBanner() {
  const { data, isLoading, isError } = useTwitchStatusQuery();

  if (isLoading) {
    return (
      <div className="vp-status-banner vp-status-banner--info">
        <Info size={16} />
        <span>Status wird ermittelt …</span>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="vp-status-banner vp-status-banner--error">
        <XCircle size={16} />
        <span>Status konnte nicht abgerufen werden.</span>
      </div>
    );
  }

  if (data.available) {
    return (
      <div className="vp-status-banner vp-status-banner--success">
        <CheckCircle2 size={16} />
        <span>VOD-Pipeline ist verfügbar.</span>
        <div className="vp-status-banner__chips">
          {data.yt_dlp_version && (
            <Badge variant="muted">yt-dlp {data.yt_dlp_version}</Badge>
          )}
          {data.ffprobe_available && <Badge variant="muted">ffprobe</Badge>}
        </div>
      </div>
    );
  }

  // Not available: show the concrete reasons so the user can fix them.
  const reasons = data.reasons.length > 0 ? data.reasons : (data.warnings ?? []);
  return (
    <div className="vp-status-banner vp-status-banner--warning">
      <AlertTriangle size={16} />
      <div className="vp-status-banner__body">
        <div className="vp-status-banner__title">
          VOD-Pipeline ist nicht vollständig verfügbar.
        </div>
        {reasons.length > 0 && (
          <ul className="vp-status-banner__reasons">
            {reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <div className="vp-status-banner__chips">
          <Badge variant={data.downloader_available ? "success" : "warning"}>
            Downloader
          </Badge>
          <Badge variant={data.ffprobe_available ? "success" : "error"}>
            ffprobe
          </Badge>
          <Badge variant={data.download_dir_writable ? "success" : "error"}>
            Download-Verzeichnis
          </Badge>
        </div>
      </div>
    </div>
  );
}
