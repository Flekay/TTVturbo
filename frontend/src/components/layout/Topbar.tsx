import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { findRouteMeta } from "../../router.routes";
import { BackendStatusPopover } from "./BackendStatusPopover";

interface TopbarProps {
  primaryAction?: ReactNode;
}

export function Topbar({ primaryAction }: TopbarProps) {
  const location = useLocation();
  const meta = findRouteMeta(location.pathname);

  const title = meta?.label ?? "TTVturbo";
  const description = meta?.description ?? "";

  return (
    <header className="app-layout__topbar" role="banner">
      <div className="topbar__title-block">
        <div className="topbar__title">{title}</div>
        {description && <div className="topbar__description">{description}</div>}
      </div>
      <div className="topbar__actions">
        <BackendStatusPopover />
        {primaryAction}
      </div>
    </header>
  );
}
