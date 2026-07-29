import { Link } from "react-router-dom";
import { ChevronRight, LoaderCircle } from "lucide-react";
import { ACTIVE_JOB_STATUSES } from "../../features/jobs/api";
import { useAllJobs } from "../../features/jobs/hooks";

export function GlobalJobDock() {
  const query = useAllJobs();
  const active = (query.data?.jobs ?? []).filter((job) => ACTIVE_JOB_STATUSES.has(job.status));
  if (active.length === 0) return null;
  return (
    <aside className="job-dock" aria-label="Aktive Vorgänge">
      <div className="job-dock__header">
        <span><LoaderCircle size={15} className="spin" /> {active.length} Vorgänge aktiv</span>
        <Link to="/jobs" aria-label="Alle Vorgänge öffnen"><ChevronRight size={16} /></Link>
      </div>
      {active.slice(0, 2).map((job) => (
        <Link className="job-dock__item" to="/jobs" key={`${job.operation}-${job.id}`}>
          <div className="job-dock__meta">
            <strong>{job.label}</strong>
            <span>{job.stage || job.sourceTitle || "Wird vorbereitet"}</span>
          </div>
          <div className="job-dock__progress" aria-label={`${job.progress ?? 0} Prozent`}>
            <span style={{ width: `${job.progress ?? 4}%` }} />
          </div>
        </Link>
      ))}
    </aside>
  );
}
