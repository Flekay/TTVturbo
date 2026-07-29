import { Link } from "react-router-dom";
import {
  ArrowRight,
  FolderKanban,
  ImageOff,
  Library,
  ListChecks,
  Loader2,
  Play,
  Plus,
  Scissors,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { EmptyState } from "../components/ui/EmptyState";
import { ACTIVE_JOB_STATUSES } from "../features/jobs/api";
import { useAllJobs } from "../features/jobs/hooks";
import { useProjects } from "../features/projects/hooks";
import { useLibraryItemsQuery } from "../features/library/hooks";
import { useBackendStatus } from "../hooks/useBackendStatus";
import { formatDateTime } from "../utils/format";
import { useUIStore } from "../stores/uiStore";

const QUICK_ACTIONS = [
  { to: "/vod-pipeline", title: "Clip erstellen", description: "Stream oder VOD verarbeiten", icon: Scissors },
  { to: "/projects", title: "Video bearbeiten", description: "Timeline-Projekt öffnen", icon: Play },
  { to: "/create/video-upscale", title: "Video hochskalieren", description: "Einzelne Datei, kein Projekt", icon: Sparkles },
  { to: "/create/background-removal", title: "Hintergrund entfernen", description: "Temporäres Schnellwerkzeug", icon: ImageOff },
  { to: "/create/text-edit", title: "Video per Text ändern", description: "Bereich oder Vollbild bearbeiten", icon: WandSparkles },
  { to: "/create/video-generation", title: "Video generieren", description: "Text- oder Bildvorlage", icon: Plus },
];

export function DashboardPage() {
  const projects = useProjects();
  const jobs = useAllJobs();
  const library = useLibraryItemsQuery();
  const backend = useBackendStatus();
  const use24h = useUIStore((state) => state.use24HourFormat);

  const recentProjects = [...(projects.data ?? [])]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 3);
  const activeJobs = (jobs.data?.jobs ?? [])
    .filter((job) => ACTIVE_JOB_STATUSES.has(job.status))
    .slice(0, 4);
  const libraryCount = library.data?.items.length ?? 0;

  return (
    <div className="page dashboard-home">
      {backend.status === "offline" && (
        <div className="system-warning" role="alert">
          <strong>Backend nicht erreichbar</strong>
          <span>Neue Vorgänge können aktuell nicht gestartet werden. Bereits geladene Ansichten bleiben verfügbar.</span>
        </div>
      )}

      <section className="home-hero">
        <div>
          <span className="eyebrow">TTVturbo</span>
          <h1>Weiterarbeiten oder etwas Neues erstellen</h1>
          <p>Einzelne Aufgaben bleiben kleine Schnellwerkzeuge. Komplexe Bearbeitung läuft über Projekte.</p>
        </div>
        <div className="home-hero__actions">
          <Link className="btn btn--primary" to="/create"><Plus size={16} /> Create öffnen</Link>
          <Link className="btn btn--secondary" to="/library"><Library size={16} /> Library</Link>
        </div>
      </section>

      <section className="home-section">
        <div className="section-heading">
          <div><h2>Schnellstart</h2><p>Direkt zur Aufgabe, ohne erst den zuständigen Backend-Service suchen zu müssen.</p></div>
          <Link to="/create">Alle Möglichkeiten <ArrowRight size={14} /></Link>
        </div>
        <div className="quick-action-grid">
          {QUICK_ACTIONS.map(({ to, title, description, icon: Icon }) => (
            <Link className="quick-action-card" to={to} key={to}>
              <span className="quick-action-card__icon"><Icon size={20} /></span>
              <span><strong>{title}</strong><small>{description}</small></span>
              <ArrowRight size={16} />
            </Link>
          ))}
        </div>
      </section>

      <div className="home-columns">
        <section className="home-section">
          <div className="section-heading">
            <div><h2>Weiterarbeiten</h2><p>Zuletzt geänderte Bearbeitungsprojekte.</p></div>
            <Link to="/projects">Alle Projekte <ArrowRight size={14} /></Link>
          </div>
          {projects.isLoading ? (
            <div className="compact-loading"><Loader2 className="spin" size={18} /> Projekte werden geladen</div>
          ) : recentProjects.length === 0 ? (
            <EmptyState
              title="Noch keine Projekte"
              description="Ein Projekt ist erst nötig, wenn du Timeline, mehrere Clips oder Ausgabevarianten brauchst."
              icon={<FolderKanban />}
              action={<Link className="btn btn--primary" to="/projects">Projekt erstellen</Link>}
            />
          ) : (
            <div className="recent-project-list">
              {recentProjects.map((project) => (
                <Link to={`/projects/${project.id}`} className="recent-project" key={project.id}>
                  <span className="recent-project__icon"><FolderKanban size={18} /></span>
                  <span className="recent-project__body">
                    <strong>{project.name}</strong>
                    <small>{project.sequence_count} Ausgaben · {project.branch_count} Varianten</small>
                  </span>
                  <time>{formatDateTime(project.updated_at, use24h)}</time>
                  <ArrowRight size={15} />
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="home-section">
          <div className="section-heading">
            <div><h2>Aktive Vorgänge</h2><p>Alle Services verwenden eine gemeinsame Job-Übersicht.</p></div>
            <Link to="/jobs">Alle Jobs <ArrowRight size={14} /></Link>
          </div>
          {jobs.isLoading ? (
            <div className="compact-loading"><Loader2 className="spin" size={18} /> Vorgänge werden geladen</div>
          ) : activeJobs.length === 0 ? (
            <div className="dashboard-idle">
              <ListChecks size={23} />
              <div><strong>Keine aktiven Vorgänge</strong><span>Gestartete Jobs erscheinen automatisch hier und unten im Job-Dock.</span></div>
            </div>
          ) : (
            <div className="active-job-list">
              {activeJobs.map((job) => (
                <Link className="active-job-row" to="/jobs" key={`${job.operation}-${job.id}`}>
                  <Loader2 size={16} className="spin" />
                  <span><strong>{job.label}</strong><small>{job.stage || job.sourceTitle || "Wird vorbereitet"}</small></span>
                  <b>{Math.round(job.progress ?? 0)}%</b>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="home-library-summary">
        <div><Library size={18} /><span><strong>{libraryCount}</strong> dauerhaft gespeicherte Medien</span></div>
        <p>Temporäre Schnellwerkzeug-Dateien werden hier nicht angezeigt.</p>
        <Link to="/library">Library öffnen <ArrowRight size={14} /></Link>
      </section>
    </div>
  );
}
