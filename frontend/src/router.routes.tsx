import type { ReactNode } from "react";
import {
  LayoutDashboard,
  Mic2,
  Film,
  Scissors,
  Lightbulb,
  AudioLines,
  Wand2,
  Video,
  PanelsTopLeft,
  Workflow,
  Send,
  Settings,
  type LucideIcon,
} from "lucide-react";

export type ModuleStatus = "available" | "partial" | "unavailable";

export interface RouteMeta {
  path: string;
  label: string;
  description: string;
  icon: LucideIcon;
  status: ModuleStatus;
  section: "main" | "production" | "admin";
}

export interface RouteSection {
  id: "main" | "production" | "admin";
  title: string;
  items: RouteMeta[];
}

export const ROUTES: RouteMeta[] = [
  {
    path: "/dashboard",
    label: "Dashboard",
    description: "Systemstatus und Aufnahmeübersicht.",
    icon: LayoutDashboard,
    status: "available",
    section: "main",
  },
  {
    path: "/voice-lab",
    label: "Voice Profiles",
    description: "Voice-Profile verwalten und Referenzen pflegen.",
    icon: Mic2,
    status: "available",
    section: "main",
  },
  {
    path: "/vod-explorer",
    label: "VOD Explorer",
    description: "Twitch-VODs erkennen, herunterladen und vorbereiten.",
    icon: Film,
    status: "unavailable",
    section: "main",
  },
  {
    path: "/clips",
    label: "Clip-Vorschläge",
    description: "Vorgeschlagene Clips aus VODs.",
    icon: Scissors,
    status: "unavailable",
    section: "main",
  },
  {
    path: "/ideas",
    label: "Ideen",
    description: "Gesprächsthemen und Inhaltsideen.",
    icon: Lightbulb,
    status: "unavailable",
    section: "main",
  },
  {
    path: "/voice-clone",
    label: "Voice Clone",
    description: "On-Demand Voice-Clone mit Qwen3-TTS.",
    icon: Wand2,
    status: "available",
    section: "production",
  },
  {
    path: "/recording-studio",
    label: "Aufnahmestudio",
    description: "Strukturierte Aufnahmesitzungen.",
    icon: AudioLines,
    status: "unavailable",
    section: "production",
  },
  {
    path: "/synthetic-studio",
    label: "Synthetic Studio",
    description: "Voice-Clones und synthetische Aufnahmen.",
    icon: Wand2,
    status: "unavailable",
    section: "production",
  },
  {
    path: "/editor",
    label: "Video Editor",
    description: "Videos zuschneiden und arrangieren.",
    icon: Video,
    status: "unavailable",
    section: "production",
  },
  {
    path: "/layouts",
    label: "Layout Studio",
    description: "Szenen und Layouts vorbereiten.",
    icon: PanelsTopLeft,
    status: "unavailable",
    section: "production",
  },
  {
    path: "/automations",
    label: "Automationen",
    description: "Wiederkehrende Abläufe automatisieren.",
    icon: Workflow,
    status: "unavailable",
    section: "admin",
  },
  {
    path: "/publishing",
    label: "Veröffentlichungen",
    description: "Fertige Clips veröffentlichen.",
    icon: Send,
    status: "unavailable",
    section: "admin",
  },
  {
    path: "/settings",
    label: "Einstellungen",
    description: "Lokale Anwendungseinstellungen.",
    icon: Settings,
    status: "partial",
    section: "admin",
  },
];

export const ROUTE_SECTIONS: RouteSection[] = [
  {
    id: "main",
    title: "Hauptbereich",
    items: ROUTES.filter((r) => r.section === "main"),
  },
  {
    id: "production",
    title: "Produktion",
    items: ROUTES.filter((r) => r.section === "production"),
  },
  {
    id: "admin",
    title: "Verwaltung",
    items: ROUTES.filter((r) => r.section === "admin"),
  },
];

export function findRouteMeta(pathname: string): RouteMeta | null {
  return ROUTES.find((r) => r.path === pathname) ?? null;
}

export function statusLabel(status: ModuleStatus): string {
  switch (status) {
    case "available":
      return "funktionsfähig";
    case "partial":
      return "teilweise funktionsfähig";
    case "unavailable":
      return "noch nicht implementiert";
  }
}

export function StatusText({
  status,
}: {
  status: ModuleStatus;
}): ReactNode {
  const labels: Record<ModuleStatus, string> = {
    available: "funktionsfähig",
    partial: "teilweise funktionsfähig",
    unavailable: "noch nicht implementiert",
  };
  return <>{labels[status]}</>;
}
