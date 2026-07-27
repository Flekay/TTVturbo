import type { ReactNode } from "react";
import {
  LayoutDashboard,
  Mic2,
  Film,
  Tv,
  Scissors,
  Lightbulb,
  AudioLines,
  Wand2,
  Video,
  PanelsTopLeft,
  Workflow,
  Send,
  Settings,
  FileText,
  type LucideIcon,
} from "lucide-react";

export type ModuleStatus = "available" | "partial" | "unavailable";

export interface RouteMeta {
  path: string;
  label: string;
  description: string;
  icon: LucideIcon;
  status: ModuleStatus;
  section: "main" | "automation" | "on_demand" | "management";
}

export interface RouteSection {
  id: "main" | "automation" | "on_demand" | "management";
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
  // --- AUTOMATION ---
  {
    path: "/vod-pipeline",
    label: "VOD Pipeline",
    description: "Download, Audio-Extraktion und Transkription automatisieren.",
    icon: Workflow,
    status: "available",
    section: "automation",
  },
  {
    path: "/clips",
    label: "Clip-Vorschläge",
    description: "Vorgeschlagene Clips aus VODs.",
    icon: Scissors,
    status: "unavailable",
    section: "automation",
  },
  {
    path: "/ideas",
    label: "Ideen",
    description: "Gesprächsthemen und Inhaltsideen.",
    icon: Lightbulb,
    status: "unavailable",
    section: "automation",
  },
  // --- ON-DEMAND TOOLS ---
  {
    path: "/vod-downloader",
    label: "VOD Downloader",
    description: "Twitch-VODs synchronisieren und herunterladen.",
    icon: Film,
    status: "available",
    section: "on_demand",
  },
  {
    path: "/transcription",
    label: "Transkription",
    description: "On-Demand Transkription mit faster-whisper.",
    icon: FileText,
    status: "available",
    section: "on_demand",
  },
  {
    path: "/voice-clone",
    label: "Voice Clone",
    description: "On-Demand Voice-Clone mit Qwen3-TTS.",
    icon: Wand2,
    status: "available",
    section: "on_demand",
  },
  {
    path: "/recording-studio",
    label: "Aufnahmestudio",
    description: "Strukturierte Aufnahmesitzungen.",
    icon: AudioLines,
    status: "unavailable",
    section: "on_demand",
  },
  {
    path: "/synthetic-studio",
    label: "Synthetic Studio",
    description: "Voice-Clones und synthetische Aufnahmen.",
    icon: Wand2,
    status: "unavailable",
    section: "on_demand",
  },
  {
    path: "/editor",
    label: "Video Editor",
    description: "Videos zuschneiden und arrangieren.",
    icon: Video,
    status: "unavailable",
    section: "on_demand",
  },
  {
    path: "/layouts",
    label: "Layout Studio",
    description: "Szenen und Layouts vorbereiten.",
    icon: PanelsTopLeft,
    status: "unavailable",
    section: "on_demand",
  },
  // --- MANAGEMENT ---
  {
    path: "/voice-profiles",
    label: "Voice Profiles",
    description: "Voice-Profile verwalten und Referenzen pflegen.",
    icon: Mic2,
    status: "available",
    section: "management",
  },
  {
    path: "/twitch-profiles",
    label: "Twitch-Profile",
    description: "Twitch-Channel-Profile verwalten.",
    icon: Tv,
    status: "available",
    section: "management",
  },
  {
    path: "/automations",
    label: "Automationen",
    description: "Wiederkehrende Abläufe automatisieren.",
    icon: Workflow,
    status: "unavailable",
    section: "management",
  },
  {
    path: "/publishing",
    label: "Veröffentlichungen",
    description: "Fertige Clips veröffentlichen.",
    icon: Send,
    status: "unavailable",
    section: "management",
  },
  {
    path: "/settings",
    label: "Einstellungen",
    description: "Lokale Anwendungseinstellungen.",
    icon: Settings,
    status: "partial",
    section: "management",
  },
];

export const ROUTE_SECTIONS: RouteSection[] = [
  {
    id: "main",
    title: "Hauptbereich",
    items: ROUTES.filter((r) => r.section === "main"),
  },
  {
    id: "automation",
    title: "Automation",
    items: ROUTES.filter((r) => r.section === "automation"),
  },
  {
    id: "on_demand",
    title: "On-Demand Werkzeuge",
    items: ROUTES.filter((r) => r.section === "on_demand"),
  },
  {
    id: "management",
    title: "Verwaltung",
    items: ROUTES.filter((r) => r.section === "management"),
  },
];

export function findRouteMeta(pathname: string): RouteMeta | null {
  // Check exact match first.
  const exact = ROUTES.find((r) => r.path === pathname);
  if (exact) return exact;
  // Check prefix match for detail pages (e.g. /vod-pipeline/:vodId).
  // We match the longest prefix that starts the pathname.
  const prefixMatches = ROUTES.filter(
    (r) => r.path !== "/" && pathname.startsWith(r.path + "/"),
  );
  if (prefixMatches.length > 0) {
    // Return the longest prefix match.
    return prefixMatches.sort((a, b) => b.path.length - a.path.length)[0];
  }
  return null;
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
