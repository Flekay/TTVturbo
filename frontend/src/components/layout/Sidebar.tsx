import { NavLink, useLocation } from "react-router-dom";
import { ChevronLeft, ChevronRight, Circle, CircleCheck, CircleDot } from "lucide-react";
import { ROUTE_SECTIONS, type RouteMeta } from "../../router.routes";
import { useUIStore } from "../../stores/uiStore";
import { useBackendStatus } from "../../hooks/useBackendStatus";
import { Tooltip } from "../ui/Tooltip";
import { Badge, type BadgeVariant } from "../ui/Badge";

function routeStatusBadge(status: RouteMeta["status"]): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "available":
      return { variant: "success", label: "funktionsfähig" };
    case "partial":
      return { variant: "warning", label: "teilweise" };
    case "unavailable":
      return { variant: "muted", label: "geplant" };
  }
}

export function Sidebar() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggle = useUIStore((s) => s.toggleSidebar);
  const { status, data } = useBackendStatus();
  const location = useLocation();

  const version = data?.version ?? "—";

  return (
    <aside className="sidebar" aria-label="Hauptnavigation">
      <div className="sidebar__header">
        <div className="sidebar__logo">
          <div className="sidebar__logo-mark" aria-hidden="true">T</div>
          <span className="sidebar__logo-text">TTVturbo</span>
        </div>
        <Tooltip content={collapsed ? "Sidebar ausklappen" : "Sidebar einklappen"} disabled={!collapsed}>
          <button
            type="button"
            className="btn btn--ghost btn--icon btn--sm sidebar__collapse-btn"
            onClick={toggle}
            aria-label={collapsed ? "Sidebar ausklappen" : "Sidebar einklappen"}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </Tooltip>
      </div>

      <nav className="sidebar__nav">
        {ROUTE_SECTIONS.map((section) => (
          <div key={section.id} className="sidebar__section">
            <div className="sidebar__section-title">{section.title}</div>
            {section.items.map((route) => {
              const Icon = route.icon;
              const badge = routeStatusBadge(route.status);
              const isActive = location.pathname === route.path;
              const link = (
                <NavLink
                  to={route.path}
                  className={`sidebar__link${isActive ? " is-active" : ""}`}
                  aria-label={route.label}
                >
                  <Icon className="sidebar__link-icon" aria-hidden="true" />
                  <span className="sidebar__link-label">{route.label}</span>
                  {!collapsed && route.status !== "available" && (
                    <Badge variant={badge.variant} title={badge.label}>
                      {badge.label}
                    </Badge>
                  )}
                </NavLink>
              );
              return collapsed ? (
                <Tooltip key={route.path} content={`${route.label} — ${badge.label}`}>
                  {link}
                </Tooltip>
              ) : (
                <div key={route.path}>
                  {link}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar__footer">
        <div className="sidebar__status-row" aria-live="polite">
          {status === "online" ? (
            <CircleCheck size={14} color="var(--color-success)" />
          ) : status === "offline" ? (
            <Circle size={14} color="var(--color-error)" />
          ) : (
            <CircleDot size={14} color="var(--color-text-muted)" />
          )}
          <span>Backend: {status === "online" ? "online" : status === "offline" ? "offline" : "verbinde …"}</span>
        </div>
        <div className="sidebar__version">Version {version}</div>
      </div>
    </aside>
  );
}
