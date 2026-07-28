import { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Clock,
  Database,
  Film,
  HardDrive,
  Mic2,
  RefreshCw,
  Tv,
  Video,
  Wand2,
} from "lucide-react";
import { useStatusQuery } from "../hooks/useQueries";
import { Badge, type BadgeVariant } from "../components/ui/Badge";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";
import { AreaChart } from "../components/charts/AreaChart";
import { DonutChart, type DonutSegment } from "../components/charts/DonutChart";
import { BarChart, type BarItem } from "../components/charts/BarChart";
import { formatBytes, formatDateTime, formatDuration, formatUptime } from "../utils/format";
import { useUIStore } from "../stores/uiStore";
import { useStatusHistoryStore } from "../stores/statusHistoryStore";
import type { BackendStatus, FeatureStatus } from "../types/status";

function featureBadge(status: FeatureStatus): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "available":
      return { variant: "success", label: "Verfügbar" };
    case "unavailable":
      return { variant: "error", label: "Nicht verfügbar" };
    case "not_implemented":
      return { variant: "muted", label: "Geplant" };
  }
}

interface ModuleDef {
  icon: React.ReactNode;
  title: string;
  description: string;
  status: FeatureStatus;
  to: string;
}

function modulesFromStatus(data: BackendStatus): ModuleDef[] {
  return [
    {
      icon: <Mic2 size={18} />,
      title: "Voice Recording",
      description: "Mikrofonaufnahmen und WAV-Konvertierung.",
      status: data.features.recording,
      to: "/voice-profiles",
    },
    {
      icon: <Wand2 size={18} />,
      title: "Voice Cloning",
      description: "Voice-Clone aus Referenzaufnahmen erzeugen.",
      status: data.features.voice_cloning,
      to: "/voice-clone",
    },
    {
      icon: <Film size={18} />,
      title: "VOD Downloader",
      description: "Twitch-VODs synchronisieren und herunterladen.",
      status: data.features.vod_downloader ?? data.features.vod_pipeline ?? "not_implemented",
      to: "/vod-downloader",
    },
    {
      icon: <Tv size={18} />,
      title: "Twitch-Profile",
      description: "Twitch-Channel-Profile verwalten.",
      status: data.features.twitch_profiles ?? "not_implemented",
      to: "/twitch-profiles",
    },
    {
      icon: <Video size={18} />,
      title: "Video Editor",
      description: "Videos zuschneiden und bearbeiten.",
      status: data.features.video_editor,
      to: "/library",
    },
    {
      icon: <Activity size={18} />,
      title: "Transkription",
      description: "Audio transkribieren und analysieren.",
      status: data.features.transcription ?? data.features.vod_analysis ?? "not_implemented",
      to: "/transcription",
    },
  ];
}

export function DashboardPage() {
  const status = useStatusQuery();
  const use24h = useUIStore((s) => s.use24HourFormat);
  const samples = useStatusHistoryStore((s) => s.samples);
  const pushSample = useStatusHistoryStore((s) => s.push);

  const data = status.data;

  useEffect(() => {
    if (!data) return;
    pushSample({
      ts: Date.now(),
      uptime_seconds: data.uptime_seconds,
      recordings_count: data.recordings.count,
      recordings_total_size_bytes: data.recordings.total_size_bytes,
      recordings_total_duration_seconds: data.recordings.total_duration_seconds,
      storage_free_bytes: data.storage.free_bytes,
    });
  }, [data, pushSample]);

  const series = useMemo(
    () => ({
      uptime: samples.map((s) => s.uptime_seconds),
      count: samples.map((s) => s.recordings_count),
      size: samples.map((s) => s.recordings_total_size_bytes),
      free: samples.map((s) => s.storage_free_bytes),
    }),
    [samples],
  );

  if (status.isLoading) {
    return <LoadingState message="Lade Systemstatus …" />;
  }
  if (status.isError || !data) {
    return (
      <ErrorState
        title="Systemstatus nicht verfügbar"
        message={
          status.error instanceof Error ? status.error.message : "Backend nicht erreichbar."
        }
        onRetry={() => void status.refetch()}
      />
    );
  }

  const isOnline = data.status === "online";
  const modules = modulesFromStatus(data);

  const usedBytes = Math.max(0, data.recordings.total_size_bytes);
  const freeBytes = Math.max(0, data.storage.free_bytes);
  const totalBytes = usedBytes + freeBytes;
  const usedPct = totalBytes > 0 ? Math.round((usedBytes / totalBytes) * 100) : 0;

  const storageSegments: DonutSegment[] = [
    { label: "Belegt", value: usedBytes, color: "var(--color-accent)" },
    { label: "Frei", value: freeBytes, color: "var(--color-success)" },
  ];

  const pipelineBars: BarItem[] = [];
  if (data.vod_pipeline) {
    const vp = data.vod_pipeline;
    pipelineBars.push(
      { label: "Profile", value: vp.profiles, color: "var(--color-info)", hint: "Twitch-Channel" },
      { label: "VODs", value: vp.vods, color: "var(--color-accent)", hint: "importiert" },
      { label: "Bereit", value: vp.ready, color: "var(--color-success)", hint: "heruntergeladen" },
    );
    if (vp.active > 0)
      pipelineBars.push({ label: "Aktiv", value: vp.active, color: "var(--color-warning)", hint: "in Arbeit" });
    if (vp.failed > 0)
      pipelineBars.push({ label: "Fehlgeschlagen", value: vp.failed, color: "var(--color-error)" });
  }

  const mediaBars: BarItem[] = [];
  if (data.media_processing) {
    const mp = data.media_processing;
    mediaBars.push(
      { label: "Audio-Artefakte", value: mp.audio_artifacts, color: "var(--color-accent)" },
      { label: "Transkripte", value: mp.transcripts, color: "var(--color-info)" },
      { label: "Audio-Jobs", value: mp.audio_jobs.total, color: "var(--color-success)", hint: `${mp.audio_jobs.ready} bereit` },
      { label: "Pipeline-Runs", value: mp.pipeline_runs.total, color: "var(--color-warning)", hint: `${mp.pipeline_runs.active} aktiv` },
    );
  }

  const availableCount = modules.filter((m) => m.status === "available").length;

  return (
    <div className="dashboard">
      {/* Hero */}
      <header className="dashboard__hero">
        <div className="dashboard__hero-title">
          <h1>Dashboard</h1>
          <span className="dashboard__hero-sub">
            {data.app_name} v{data.version} · {availableCount}/{modules.length} Module aktiv
          </span>
        </div>
        <div className="dashboard__hero-meta">
          <span className="dashboard__live">
            <span
              className={`dashboard__live-dot ${isOnline ? "" : "dashboard__live-dot--offline"}`}
              aria-hidden="true"
            />
            {isOnline ? "Online" : "Offline"}
          </span>
          <span className="dashboard__updated">
            Aktualisiert {formatDateTime(new Date().toISOString(), use24h)}
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void status.refetch()}
            aria-label="Aktualisieren"
          >
            <RefreshCw size={14} /> Aktualisieren
          </button>
        </div>
      </header>

      {/* KPI tiles */}
      <section className="dashboard__kpis">
        <KpiTile
          icon={<Activity size={16} />}
          label="Backend"
          value={isOnline ? "Online" : "Offline"}
          sub={data.app_name}
          sparkData={series.uptime}
          sparkColor="var(--color-success)"
          formatValue={(v) => formatUptime(v)}
        />
        <KpiTile
          icon={<Clock size={16} />}
          label="Laufzeit"
          value={formatUptime(data.uptime_seconds)}
          sub="seit Serverstart"
          sparkData={series.uptime}
          sparkColor="var(--color-accent)"
          formatValue={(v) => formatUptime(v)}
        />
        <KpiTile
          icon={<Mic2 size={16} />}
          label="Aufnahmen"
          value={String(data.recordings.count)}
          sub={formatDuration(data.recordings.total_duration_seconds)}
          sparkData={series.count}
          sparkColor="var(--color-info)"
          formatValue={(v) => String(Math.round(v))}
        />
        <KpiTile
          icon={<HardDrive size={16} />}
          label="Freier Speicher"
          value={formatBytes(data.storage.free_bytes)}
          sub={`${usedPct}% belegt`}
          sparkData={series.free}
          sparkColor="var(--color-warning)"
          formatValue={(v) => formatBytes(v)}
        />
      </section>

      {/* Charts row */}
      <section className="dashboard__charts">
        <div className="chart-card">
          <div className="chart-card__head">
            <div>
              <div className="chart-card__title">Aufnahmen-Verlauf</div>
              <div className="chart-card__subtitle">
                Anzahl gespeicherter Aufnahmen über die Sitzung
              </div>
            </div>
            <Badge variant="info">{samples.length} Samples</Badge>
          </div>
          <div className="chart-card__body">
            {series.count.length > 1 ? (
              <AreaChart
                data={series.count}
                height={180}
                color="var(--color-accent)"
                fillId="dash-area-count"
                formatValue={(v) => `${Math.round(v)}`}
                ariaLabel="Aufnahmen-Verlauf"
              />
            ) : (
              <div className="dash-empty">
                <Database size={28} />
                <span>Sammelt Daten … Graphen erscheinen nach wenigen Sekunden.</span>
              </div>
            )}
          </div>
        </div>

        <div className="chart-card storage-card">
          <div className="chart-card__head">
            <div>
              <div className="chart-card__title">Speicherbelegung</div>
              <div className="chart-card__subtitle">Aufnahmemedium</div>
            </div>
          </div>
          <div className="storage-card__body">
            <div className="storage-card__donut">
              <DonutChart
                segments={storageSegments}
                centerLabel={formatBytes(totalBytes)}
                centerSub="Gesamt"
                ariaLabel="Speicherbelegung"
              />
            </div>
            <div className="storage-card__legend">
              <div className="legend-item">
                <span className="legend-item__swatch" style={{ backgroundColor: "var(--color-accent)" }} />
                <span className="legend-item__label">Belegt</span>
                <span className="legend-item__value">{formatBytes(usedBytes)}</span>
              </div>
              <div className="legend-item">
                <span className="legend-item__swatch" style={{ backgroundColor: "var(--color-success)" }} />
                <span className="legend-item__label">Frei</span>
                <span className="legend-item__value">{formatBytes(freeBytes)}</span>
              </div>
              <div className="legend-item">
                <span className="legend-item__swatch" style={{ backgroundColor: "transparent", border: "1px solid var(--color-border)" }} />
                <span className="legend-item__label">Belegung</span>
                <span className="legend-item__value">{usedPct}%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Pipeline / media bar charts */}
      {(pipelineBars.length > 0 || mediaBars.length > 0) && (
        <section className="dashboard__charts">
          {pipelineBars.length > 0 && (
            <div className="chart-card">
              <div className="chart-card__head">
                <div>
                  <div className="chart-card__title">VOD Pipeline</div>
                  <div className="chart-card__subtitle">Profile, Downloads & Status</div>
                </div>
                <Link className="dash-module__link" to="/vod-downloader">
                  Öffnen <ArrowRight size={12} />
                </Link>
              </div>
              <div className="chart-card__body">
                <BarChart bars={pipelineBars} ariaLabel="VOD Pipeline Status" />
              </div>
            </div>
          )}
          {mediaBars.length > 0 && (
            <div className="chart-card">
              <div className="chart-card__head">
                <div>
                  <div className="chart-card__title">Media Processing</div>
                  <div className="chart-card__subtitle">Audio, Transkripte & Pipeline-Runs</div>
                </div>
                <Link className="dash-module__link" to="/transcription">
                  Öffnen <ArrowRight size={12} />
                </Link>
              </div>
              <div className="chart-card__body">
                <BarChart bars={mediaBars} ariaLabel="Media Processing Status" />
              </div>
            </div>
          )}
        </section>
      )}

      {/* Modules */}
      <section>
        <div className="dashboard__section-head">
          <span className="dashboard__section-title">Module</span>
          <span className="dashboard__section-sub">{availableCount} aktiv · {modules.length} gesamt</span>
        </div>
        <div className="dashboard__modules">
          {modules.map((m) => {
            const badge = featureBadge(m.status);
            return (
              <div key={m.title} className="dash-module">
                <div className="dash-module__head">
                  <div className="dash-module__icon" aria-hidden="true">{m.icon}</div>
                  <div className="dash-module__title">{m.title}</div>
                </div>
                <div className="dash-module__desc">{m.description}</div>
                <div className="dash-module__foot">
                  <Badge variant={badge.variant}>{badge.label}</Badge>
                  <Link className="dash-module__link" to={m.to}>
                    Öffnen <ArrowRight size={12} />
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

interface KpiTileProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  sparkData: number[];
  sparkColor: string;
  formatValue: (v: number) => string;
}

function KpiTile({ icon, label, value, sub, sparkData, sparkColor, formatValue }: KpiTileProps) {
  const fillId = `kpi-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="kpi-tile">
      <div className="kpi-tile__head">
        <span className="kpi-tile__label">{label}</span>
        <span className="kpi-tile__icon" aria-hidden="true">{icon}</span>
      </div>
      <div className="kpi-tile__value">{value}</div>
      <div className="kpi-tile__sub">{sub}</div>
      {sparkData.length > 1 ? (
        <div className="kpi-tile__spark">
          <AreaChart
            data={sparkData}
            height={44}
            color={sparkColor}
            fillId={fillId}
            formatValue={formatValue}
            ariaLabel={`${label}-Verlauf`}
          />
        </div>
      ) : null}
    </div>
  );
}
