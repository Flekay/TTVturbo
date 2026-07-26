import { Badge } from "../../components/ui/Badge";
import type { VoiceProfileProgress } from "./types";

interface RecordingPackProgressProps {
  progress: VoiceProfileProgress;
}

/**
 * Renders the server-provided progress breakdown for a voice profile.
 *
 * No values are invented: every number comes from
 * `VoiceProfileProgress`. `clone_ready` and `pack_complete` are shown as
 * explicit badges so colour is not the only signal.
 */
export function RecordingPackProgress({ progress }: RecordingPackProgressProps) {
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent ?? 0)));
  return (
    <div className="vp-progress" role="group" aria-label="Aufnahmefortschritt">
      <div className="vp-progress__bar" aria-hidden="true">
        <div className="vp-progress__bar-fill" style={{ width: `${percent}%` }} />
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Akzeptiert</span>
        <span className="vp-progress__stat-value">{progress.accepted}</span>
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Review</span>
        <span className="vp-progress__stat-value">{progress.review}</span>
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Abgelehnt</span>
        <span className="vp-progress__stat-value">{progress.rejected}</span>
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Fehlend</span>
        <span className="vp-progress__stat-value">{progress.missing}</span>
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Gesamt</span>
        <span className="vp-progress__stat-value">{progress.total}</span>
      </div>
      <div className="vp-progress__stat">
        <span className="vp-progress__stat-label">Prozent</span>
        <span className="vp-progress__stat-value">{percent}%</span>
      </div>
      <div
        className="vp-progress__stat"
        aria-label={`Clone bereit: ${progress.clone_ready ? "ja" : "nein"}`}
      >
        <span className="vp-progress__stat-label">Clone-ready</span>
        <span className="vp-progress__stat-value">
          <Badge variant={progress.clone_ready ? "success" : "muted"}>
            {progress.clone_ready ? "Ja" : "Nein"}
          </Badge>
        </span>
      </div>
      <div
        className="vp-progress__stat"
        aria-label={`Pack vollständig: ${progress.pack_complete ? "ja" : "nein"}`}
      >
        <span className="vp-progress__stat-label">Pack vollständig</span>
        <span className="vp-progress__stat-value">
          <Badge variant={progress.pack_complete ? "success" : "muted"}>
            {progress.pack_complete ? "Ja" : "Nein"}
          </Badge>
        </span>
      </div>
    </div>
  );
}
