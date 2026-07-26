import { useState } from "react";
import { Download, Trash2 } from "lucide-react";
import type { Recording } from "../../types/recording";
import { useUIStore } from "../../stores/uiStore";
import { Button } from "../ui/Button";

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes)) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatCreatedAt(iso: string, use24h: boolean): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: !use24h,
    });
  } catch {
    return iso;
  }
}

interface RecordingCardProps {
  recording: Recording;
  onDeleteRequest: (recording: Recording) => void;
  deleting: boolean;
}

export function RecordingCard({ recording, onDeleteRequest, deleting }: RecordingCardProps) {
  const use24h = useUIStore((s) => s.use24HourFormat);
  const confirmDelete = useUIStore((s) => s.confirmDelete);
  const [busy, setBusy] = useState(false);

  const audioSrc = `${recording.audio_url}?t=${Date.now()}`;

  const handleDelete = () => {
    if (confirmDelete) {
      onDeleteRequest(recording);
    } else {
      setBusy(true);
      // Without confirmation we still go through the parent mutation.
      onDeleteRequest(recording);
    }
  };

  return (
    <li className="recording-card">
      <div className="recording-card__meta">
        <div className="recording-card__filename" title={recording.filename}>
          {recording.filename}
        </div>
        <div className="recording-card__info">
          <span>{formatCreatedAt(recording.created_at, use24h)}</span>
          <span>Dauer: {formatDuration(recording.duration_seconds)}</span>
          <span>Größe: {formatSize(recording.file_size_bytes)}</span>
        </div>
      </div>
      <div className="recording-card__actions">
        <a
          className="btn btn--secondary btn--sm"
          href={recording.audio_url}
          download={recording.filename}
          aria-label={`${recording.filename} herunterladen`}
        >
          <Download size={14} /> Download
        </a>
        <Button
          variant="danger"
          size="sm"
          onClick={handleDelete}
          loading={deleting && busy}
          aria-label={`${recording.filename} löschen`}
        >
          <Trash2 size={14} /> Löschen
        </Button>
      </div>
      <div className="recording-card__player-row">
        <audio
          className="recording-card__audio"
          controls
          src={audioSrc}
          preload="none"
          aria-label={`Audio-Player für ${recording.filename}`}
        />
      </div>
    </li>
  );
}
