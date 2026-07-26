import { Link } from "react-router-dom";
import { Mic2, Wand2, Film, Video, ArrowRight } from "lucide-react";
import { useStatusQuery } from "../hooks/useQueries";
import { useRecordingsQuery } from "../hooks/useQueries";
import { Card } from "../components/ui/Card";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";
import { formatBytes, formatDuration, formatUptime, formatDateTime } from "../utils/format";
import { useUIStore } from "../stores/uiStore";
import type { FeatureStatus } from "../types/status";

function featureBadge(status: FeatureStatus): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "available":
      return { variant: "success", label: "Verfügbar" };
    case "unavailable":
      return { variant: "error", label: "Nicht verfügbar" };
    case "not_implemented":
      return { variant: "muted", label: "Noch nicht implementiert" };
  }
}

export function DashboardPage() {
  const status = useStatusQuery();
  const recordings = useRecordingsQuery();
  const use24h = useUIStore((s) => s.use24HourFormat);

  if (status.isLoading) {
    return <LoadingState message="Lade Systemstatus …" />;
  }
  if (status.isError || !status.data) {
    return (
      <ErrorState
        title="Systemstatus nicht verfügbar"
        message={
          status.error instanceof Error
            ? status.error.message
            : "Backend nicht erreichbar."
        }
        onRetry={() => void status.refetch()}
      />
    );
  }

  const data = status.data;
  const recent = (recordings.data?.recordings ?? []).slice(0, 5);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Dashboard</h1>
          <p className="page__description">
            Echtzeit-Übersicht über Backend, Aufnahmen und verfügbare Module.
          </p>
        </div>
      </div>

      <section className="page__section">
        <h2 className="page__section-title">Systemstatus</h2>
        <div className="page__grid">
          <Card title="Backendstatus" value={data.status} sub={data.app_name} />
          <Card title="App-Version" value={data.version} sub="TTVturbo" />
          <Card
            title="Laufzeit"
            value={formatUptime(data.uptime_seconds)}
            sub="seit Serverstart"
          />
          <Card
            title="Freier Speicher"
            value={formatBytes(data.storage.free_bytes)}
            sub="Aufnahmemedium"
          />
        </div>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Aufnahmen</h2>
        <div className="page__grid">
          <Card
            title="Anzahl Aufnahmen"
            value={data.recordings.count}
            sub="gespeicherte WAVs"
          />
          <Card
            title="Gesamtdauer"
            value={formatDuration(data.recordings.total_duration_seconds)}
            sub="alle Aufnahmen"
          />
          <Card
            title="Belegter Speicher"
            value={formatBytes(data.recordings.total_size_bytes)}
            sub="Aufnahmen gesamt"
          />
        </div>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Module</h2>
        <div className="page__grid">
          <ModuleCard
            icon={<Mic2 size={18} />}
            title="Voice Recording"
            description="Mikrofonaufnahmen und WAV-Konvertierung."
            status={data.features.recording}
          />
          <ModuleCard
            icon={<Wand2 size={18} />}
            title="Voice Cloning"
            description="Voice-Clone aus Referenzaufnahmen erzeugen."
            status={data.features.voice_cloning}
          />
          <ModuleCard
            icon={<Film size={18} />}
            title="VOD Explorer"
            description="Twitch-VODs erkennen und herunterladen."
            status={data.features.vod_analysis}
          />
          <ModuleCard
            icon={<Video size={18} />}
            title="Video Editor"
            description="Videos zuschneiden und bearbeiten."
            status={data.features.video_editor}
          />
        </div>
      </section>

      <section className="page__section">
        <h2 className="page__section-title">Letzte Aufnahmen</h2>
        {recordings.isLoading ? (
          <LoadingState message="Lade Aufnahmen …" />
        ) : recordings.isError ? (
          <ErrorState
            title="Aufnahmen konnten nicht geladen werden"
            onRetry={() => void recordings.refetch()}
          />
        ) : recent.length === 0 ? (
          <Card>
            <p style={{ color: "var(--color-text-secondary)" }}>
              Noch keine Aufnahmen vorhanden. Lade eine Referenz im Voice Clone hoch, um Statistiken zu sehen.
            </p>
            <Link className="btn btn--primary" to="/voice-clone" style={{ alignSelf: "flex-start" }}>
              Zum Voice Clone <ArrowRight size={14} />
            </Link>
          </Card>
        ) : (
          <ul className="recent-list">
            {recent.map((rec) => (
              <li key={rec.filename} className="recent-item">
                <div className="recent-item__meta">
                  <div className="recent-item__date">
                    {formatDateTime(rec.created_at, use24h)}
                  </div>
                  <div className="recent-item__sub">
                    {formatDuration(rec.duration_seconds)} · {formatBytes(rec.file_size_bytes)}
                  </div>
                </div>
                <audio
                  className="recent-item__audio"
                  controls
                  src={rec.audio_url}
                  preload="none"
                  aria-label={`Audio-Player für ${rec.filename}`}
                />
                <Link
                  to="/voice-lab"
                  className="btn btn--ghost btn--sm"
                  aria-label="Voice Profiles öffnen"
                >
                  Voice Profiles <ArrowRight size={14} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="page__section">
        <Card>
          <h3 style={{ fontSize: 16, marginBottom: 8 }}>Entwicklungsstatus</h3>
          <p style={{ color: "var(--color-text-secondary)", marginBottom: 8 }}>
            <strong>Nächster Entwicklungsschritt:</strong> Aus einer gespeicherten Aufnahme mit
            Qwen3-TTS einen echten Voice Clone erzeugen.
          </p>
          <p style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
            Es werden keine simulierten Fortschrittswerte angezeigt – nur reale Funktionen sind
            aktiv.
          </p>
        </Card>
      </section>
    </div>
  );
}

interface ModuleCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  status: FeatureStatus;
}

function ModuleCard({ icon, title, description, status }: ModuleCardProps) {
  const badge = featureBadge(status);
  return (
    <div className="module-card">
      <div className="module-card__icon" aria-hidden="true">
        {icon}
      </div>
      <div className="module-card__body">
        <div className="module-card__title">{title}</div>
        <div className="module-card__desc">{description}</div>
        <div style={{ marginTop: 6 }}>
          <Badge variant={badge.variant}>{badge.label}</Badge>
        </div>
      </div>
    </div>
  );
}

