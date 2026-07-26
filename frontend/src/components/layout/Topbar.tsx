import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { findRouteMeta } from "../../router.routes";
import { useBackendStatus } from "../../hooks/useBackendStatus";

interface TopbarProps {
  primaryAction?: ReactNode;
}

export function Topbar({ primaryAction }: TopbarProps) {
  const location = useLocation();
  const meta = findRouteMeta(location.pathname);
  const { status } = useBackendStatus();

  const title = meta?.label ?? "TTVturbo";
  const description = meta?.description ?? "";

  const statusClass =
    status === "online" ? "is-online" : status === "offline" ? "is-offline" : "";
  const statusText =
    status === "online" ? "online" : status === "offline" ? "offline" : "verbinde …";

  return (
    <header className="app-layout__topbar" role="banner">
      <div className="topbar__title-block">
        <div className="topbar__title">{title}</div>
        {description && <div className="topbar__description">{description}</div>}
      </div>
      <div className="topbar__actions">
        <div
          className={`topbar__status ${statusClass}`}
          role="status"
          aria-live="polite"
          title={`Backendstatus: ${statusText}`}
        >
          <span className="topbar__status-dot" aria-hidden="true" />
          <span>{statusText}</span>
        </div>
        {primaryAction}
      </div>
    </header>
  );
}
