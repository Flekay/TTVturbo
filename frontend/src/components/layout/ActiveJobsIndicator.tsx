import { Link } from "react-router-dom";
import { LoaderCircle } from "lucide-react";
import { ACTIVE_JOB_STATUSES } from "../../features/jobs/api";
import { useAllJobs } from "../../features/jobs/hooks";

export function ActiveJobsIndicator() {
  const query = useAllJobs();
  const active = (query.data?.jobs ?? []).filter((job) => ACTIVE_JOB_STATUSES.has(job.status));
  if (active.length === 0) return null;
  return (
    <Link className="topbar__job-indicator" to="/jobs" aria-label={`${active.length} aktive Vorgänge`}>
      <LoaderCircle size={15} className="spin" />
      <span>{active.length} aktiv</span>
    </Link>
  );
}
