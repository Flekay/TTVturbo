import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Download,
  Loader2,
  RefreshCw,
  RotateCcw,
  Square,
} from "lucide-react";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { ACTIVE_JOB_STATUSES, type UnifiedJob } from "../features/jobs/api";
import { useAllJobs, useCancelJob, useRetryJob } from "../features/jobs/hooks";
import { formatDateTime } from "../utils/format";
import { useUIStore } from "../stores/uiStore";

const TERMINAL_SUCCESS = new Set(["COMPLETED", "READY", "DONE", "READY_FOR_CLIP_ANALYSIS"]);
const TERMINAL_FAILED = new Set(["FAILED", "CANCELED", "CANCELLED"]);

function statusLabel(status: string): string {
  switch (status) {
    case "QUEUED": return "Wartet";
    case "RUNNING": return "Aktiv";
    case "RETRYING": return "Neustart";
    case "WAITING_FOR_GPU": return "Wartet auf GPU";
    case "WAITING_FOR_DEPENDENCY": return "Wartet auf Vorarbeit";
    case "EXPORTING": return "Exportiert";
    case "COMPLETED":
    case "READY":
    case "READY_FOR_CLIP_ANALYSIS": return "Fertig";
    case "FAILED": return "Fehlgeschlagen";
    case "CANCELED":
    case "CANCELLED": return "Abgebrochen";
    default: return status;
  }
}

function JobStatusIcon({ job }: { job: UnifiedJob }) {
  if (ACTIVE_JOB_STATUSES.has(job.status)) return <Loader2 size={18} className="spin" />;
  if (TERMINAL_SUCCESS.has(job.status)) return <CheckCircle2 size={18} />;
  if (TERMINAL_FAILED.has(job.status)) return <AlertCircle size={18} />;
  return <Clock3 size={18} />;
}

export function JobsPage() {
  const [tab, setTab] = useState<"active" | "history">("active");
  const [filter, setFilter] = useState("all");
  const query = useAllJobs();
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const use24h = useUIStore((state) => state.use24HourFormat);

  const jobs = useMemo(() => {
    const all = query.data?.jobs ?? [];
    return all.filter((job) => {
      const active = ACTIVE_JOB_STATUSES.has(job.status);
      if (tab === "active" && !active) return false;
      if (tab === "history" && active) return false;
      return filter === "all" || job.operation === filter;
    });
  }, [filter, query.data?.jobs, tab]);

  const operations = useMemo(() => {
    const values = new Map<string, string>();
    for (const job of query.data?.jobs ?? []) values.set(job.operation, job.label);
    return Array.from(values.entries());
  }, [query.data?.jobs]);

  if (query.isError) {
    return <ErrorState title="Vorgänge konnten nicht geladen werden" message={query.error instanceof Error ? query.error.message : "Unbekannter Fehler"} onRetry={() => void query.refetch()} />;
  }

  return (
    <div className="page jobs-page">
      <div className="page-toolbar">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "active"} className={tab === "active" ? "is-active" : ""} onClick={() => setTab("active")}>Aktiv</button>
          <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "is-active" : ""} onClick={() => setTab("history")}>Verlauf</button>
        </div>
        <div className="page-toolbar__actions">
          <select className="input compact-select" value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Vorgangstyp filtern">
            <option value="all">Alle Vorgänge</option>
            {operations.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <Button variant="ghost" size="sm" onClick={() => void query.refetch()}><RefreshCw size={14} /> Aktualisieren</Button>
        </div>
      </div>

      {query.isLoading ? (
        <div className="state"><Loader2 className="spin" /><span>Vorgänge werden geladen …</span></div>
      ) : jobs.length === 0 ? (
        <EmptyState title={tab === "active" ? "Keine aktiven Vorgänge" : "Noch kein Verlauf"} description={tab === "active" ? "Neue Verarbeitungen erscheinen automatisch hier und unten rechts in der Jobleiste." : "Abgeschlossene, fehlgeschlagene und abgebrochene Vorgänge werden hier gesammelt."} />
      ) : (
        <div className="jobs-list">
          {jobs.map((job) => (
            <article className={`job-row job-row--${job.status.toLowerCase()}`} key={`${job.operation}-${job.id}`}>
              <div className="job-row__icon"><JobStatusIcon job={job} /></div>
              <div className="job-row__main">
                <div className="job-row__title"><strong>{job.label}</strong><span className="job-row__status">{statusLabel(job.status)}</span></div>
                <div className="job-row__subtitle">{job.sourceTitle || job.stage || job.id}</div>
                {ACTIVE_JOB_STATUSES.has(job.status) && (
                  <div className="job-row__progress"><span style={{ width: `${Math.max(3, job.progress ?? 0)}%` }} /></div>
                )}
                {job.errorMessage && <div className="job-row__error">{job.errorMessage}</div>}
              </div>
              <div className="job-row__meta">
                <span>{job.progress != null ? `${Math.round(job.progress)} %` : ""}</span>
                {job.createdAt && <time>{formatDateTime(job.createdAt, use24h)}</time>}
              </div>
              <div className="job-row__actions">
                {job.libraryItemId && TERMINAL_SUCCESS.has(job.status) && <a className="btn btn--ghost btn--sm" href={`/api/library/items/${encodeURIComponent(job.libraryItemId)}/file`}><Download size={14} /> Ergebnis</a>}
                {ACTIVE_JOB_STATUSES.has(job.status) && <Button variant="danger" size="sm" onClick={() => cancel.mutate(job)} loading={cancel.isPending}><Square size={13} /> Abbrechen</Button>}
                {job.retryable && TERMINAL_FAILED.has(job.status) && <Button size="sm" onClick={() => retry.mutate(job)} loading={retry.isPending}><RotateCcw size={14} /> Wiederholen</Button>}
              </div>
            </article>
          ))}
        </div>
      )}

      {query.data?.unavailable.length ? (
        <div className="jobs-footnote">Einige ältere Backend-Bereiche konnten nicht abgefragt werden. Verfügbare Vorgänge werden trotzdem vollständig angezeigt.</div>
      ) : null}

      <div className="jobs-create-link">Neue Verarbeitung starten: <Link to="/create">Create öffnen</Link></div>
    </div>
  );
}
