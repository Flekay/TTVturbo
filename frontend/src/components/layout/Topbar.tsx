import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { findRouteMeta } from "../../router.routes";
import { useBackendStatus } from "../../hooks/useBackendStatus";
import { ActiveJobsIndicator } from "./ActiveJobsIndicator";

interface TopbarProps { primaryAction?: ReactNode; }

export function Topbar({ primaryAction }: TopbarProps) {
  const location = useLocation();
  const meta = findRouteMeta(location.pathname);
  const backend = useBackendStatus();
  return (
    <header className="app-layout__topbar" role="banner">
      <div className="topbar__title-block">
        <div className="topbar__title">{meta?.label ?? "TTVturbo"}</div>
        {meta?.description && <div className="topbar__description">{meta.description}</div>}
      </div>
      <div className="topbar__actions">
        {backend.status === "offline" && (
          <span className="topbar__problem" role="alert"><AlertTriangle size={14} /> Backend offline</span>
        )}
        <ActiveJobsIndicator />
        {primaryAction}
      </div>
    </header>
  );
}
