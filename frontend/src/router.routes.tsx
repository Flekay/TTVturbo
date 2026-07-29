import {
  BriefcaseBusiness,
  FolderKanban,
  LayoutDashboard,
  Library,
  ListChecks,
  Plus,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export interface RouteMeta {
  path: string;
  label: string;
  description: string;
  icon: LucideIcon;
  navigation?: boolean;
}

export const ROUTES: RouteMeta[] = [
  {
    path: "/dashboard",
    label: "Dashboard",
    description: "Weiterarbeiten, Schnellstart und aktive Vorgänge.",
    icon: LayoutDashboard,
    navigation: true,
  },
  {
    path: "/library",
    label: "Library",
    description: "Dauerhaft gespeicherte Medien und Versionen.",
    icon: Library,
    navigation: true,
  },
  {
    path: "/create",
    label: "Create",
    description: "Schnellwerkzeuge und geführte Workflows.",
    icon: Plus,
    navigation: true,
  },
  {
    path: "/projects",
    label: "Projects",
    description: "Bearbeitungsprojekte, Ausgaben und Versionen.",
    icon: FolderKanban,
    navigation: true,
  },
  {
    path: "/jobs",
    label: "Jobs",
    description: "Aktive und abgeschlossene Vorgänge.",
    icon: ListChecks,
    navigation: true,
  },
  {
    path: "/settings",
    label: "Settings",
    description: "Profile, Modelle, Speicher und Systemstatus.",
    icon: Settings,
    navigation: true,
  },
  {
    path: "/create/video-upscale",
    label: "Video hochskalieren",
    description: "Ein Video temporär hochskalieren und optional speichern.",
    icon: Sparkles,
  },
  {
    path: "/create/background-removal",
    label: "Hintergrund entfernen",
    description: "Vordergrund freistellen oder einen neuen Hintergrund einsetzen.",
    icon: Sparkles,
  },
  {
    path: "/create/text-edit",
    label: "Video per Text bearbeiten",
    description: "Bereiche oder das vollständige Video mit einer Anweisung verändern.",
    icon: Sparkles,
  },
  {
    path: "/create/video-generation",
    label: "Video generieren",
    description: "Ein Video aus Text oder einem Referenzbild erzeugen.",
    icon: Sparkles,
  },
  {
    path: "/vod-pipeline",
    label: "Clip aus VOD",
    description: "VOD auswählen, verarbeiten und weiterverwenden.",
    icon: BriefcaseBusiness,
  },
  {
    path: "/transcription",
    label: "Transkription",
    description: "Audio oder Video transkribieren.",
    icon: BriefcaseBusiness,
  },
  {
    path: "/voice-clone",
    label: "Voiceover",
    description: "Text mit einem Voice-Profil erzeugen.",
    icon: BriefcaseBusiness,
  },
  {
    path: "/voice-profiles",
    label: "Voice-Profile",
    description: "Referenzen und Voice-Profile verwalten.",
    icon: Settings,
  },
  {
    path: "/twitch-profiles",
    label: "Twitch-Profile",
    description: "Twitch-Kanäle und Synchronisierung verwalten.",
    icon: Settings,
  },
  {
    path: "/vod-downloader",
    label: "VOD Downloader",
    description: "Twitch-VODs synchronisieren und herunterladen.",
    icon: BriefcaseBusiness,
  },
];

export const NAVIGATION_ROUTES = ROUTES.filter((route) => route.navigation);

export function findRouteMeta(pathname: string): RouteMeta | null {
  const exact = ROUTES.find((route) => route.path === pathname);
  if (exact) return exact;
  const matches = ROUTES.filter(
    (route) => route.path !== "/" && pathname.startsWith(`${route.path}/`),
  );
  if (matches.length === 0) return null;
  return matches.sort((a, b) => b.path.length - a.path.length)[0];
}
