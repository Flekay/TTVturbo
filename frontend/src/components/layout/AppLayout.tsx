import type { ReactNode } from "react";
import { useUIStore } from "../../stores/uiStore";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { GlobalJobDock } from "./GlobalJobDock";

interface AppLayoutProps {
  children: ReactNode;
  primaryAction?: ReactNode;
}

export function AppLayout({ children, primaryAction }: AppLayoutProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const topbarHidden = useUIStore((s) => s.topbarHidden);
  const classes = ["app-layout", collapsed && "is-collapsed", topbarHidden && "is-topbar-hidden"]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={classes}>
      <Sidebar />
      {!topbarHidden && <Topbar primaryAction={primaryAction} />}
      <main className="app-layout__main" id="main-content">
        {children}
      </main>
      <GlobalJobDock />
    </div>
  );
}
