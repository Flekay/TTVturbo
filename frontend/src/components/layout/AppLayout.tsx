import type { ReactNode } from "react";
import { useUIStore } from "../../stores/uiStore";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

interface AppLayoutProps {
  children: ReactNode;
  primaryAction?: ReactNode;
}

export function AppLayout({ children, primaryAction }: AppLayoutProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  return (
    <div className={`app-layout${collapsed ? " is-collapsed" : ""}`}>
      <Sidebar />
      <Topbar primaryAction={primaryAction} />
      <main className="app-layout__main" id="main-content">
        {children}
      </main>
    </div>
  );
}
