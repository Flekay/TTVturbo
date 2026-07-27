import { NavLink, useLocation } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { ROUTE_SECTIONS, type RouteMeta } from "../../router.routes";
import { useUIStore } from "../../stores/uiStore";
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
  const location = useLocation();

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
    </aside>
  );
}
