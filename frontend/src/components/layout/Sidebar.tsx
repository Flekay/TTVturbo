import { NavLink } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { NAVIGATION_ROUTES } from "../../router.routes";
import { useUIStore } from "../../stores/uiStore";
import { Tooltip } from "../ui/Tooltip";

export function Sidebar() {
  const collapsed = useUIStore((state) => state.sidebarCollapsed);
  const toggle = useUIStore((state) => state.toggleSidebar);

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

      <nav className="sidebar__nav sidebar__nav--primary">
        {NAVIGATION_ROUTES.map((route) => {
          const Icon = route.icon;
          const link = (
            <NavLink
              to={route.path}
              className={({ isActive }) => `sidebar__link${isActive ? " is-active" : ""}`}
              aria-label={route.label}
            >
              <Icon className="sidebar__link-icon" aria-hidden="true" />
              <span className="sidebar__link-label">{route.label}</span>
            </NavLink>
          );
          return collapsed ? (
            <Tooltip key={route.path} content={route.label}>{link}</Tooltip>
          ) : (
            <div key={route.path}>{link}</div>
          );
        })}
      </nav>
    </aside>
  );
}
