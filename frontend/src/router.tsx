import { useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppLayout } from "./components/layout/AppLayout";
import { CreatePage } from "./pages/CreatePage";
import { DashboardPage } from "./pages/DashboardPage";
import { JobsPage } from "./pages/JobsPage";
import { LibraryDetailPage } from "./pages/LibraryDetailPage";
import { LibraryPage } from "./pages/LibraryPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProjectWorkspacePage } from "./pages/ProjectWorkspacePage";
import { QuickToolPage } from "./pages/QuickToolPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TranscriptionPage } from "./pages/TranscriptionPage";
import { TwitchProfilesPage } from "./pages/TwitchProfilesPage";
import { VodDetailPage } from "./pages/VodDetailPage";
import { VodDownloaderPage } from "./pages/VodDownloaderPage";
import { VodPipelinePage } from "./pages/VodPipelinePage";
import { VoiceClonePage } from "./pages/VoiceClonePage";
import { VoiceProfilesPage } from "./pages/VoiceProfilesPage";
import { findRouteMeta } from "./router.routes";

function useDocumentTitle() {
  const location = useLocation();
  useEffect(() => {
    const meta = findRouteMeta(location.pathname);
    document.title = meta ? `${meta.label} – TTVturbo` : "TTVturbo";
  }, [location.pathname]);
}

function WithLayout({ children }: { children: ReactNode }) {
  return <AppLayout>{children}</AppLayout>;
}

export function AppRouter() {
  useDocumentTitle();

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<WithLayout><DashboardPage /></WithLayout>} />
      <Route path="/library" element={<WithLayout><LibraryPage /></WithLayout>} />
      <Route path="/library/:itemId" element={<WithLayout><LibraryDetailPage /></WithLayout>} />
      <Route path="/create" element={<WithLayout><CreatePage /></WithLayout>} />
      <Route path="/create/video-upscale" element={<WithLayout><QuickToolPage tool="video-upscale" /></WithLayout>} />
      <Route path="/create/background-removal" element={<WithLayout><QuickToolPage tool="video-background-removal" /></WithLayout>} />
      <Route path="/create/text-edit" element={<WithLayout><QuickToolPage tool="video-text-edit" /></WithLayout>} />
      <Route path="/create/video-cut" element={<WithLayout><QuickToolPage tool="video-cut" /></WithLayout>} />
      <Route path="/create/video-generation" element={<WithLayout><QuickToolPage tool="video-generation" /></WithLayout>} />
      <Route path="/projects" element={<WithLayout><ProjectsPage /></WithLayout>} />
      <Route path="/projects/:projectId" element={<WithLayout><ProjectWorkspacePage /></WithLayout>} />
      <Route path="/jobs" element={<WithLayout><JobsPage /></WithLayout>} />
      <Route path="/settings" element={<WithLayout><SettingsPage /></WithLayout>} />

      {/* Contextual legacy pages remain reachable from Create and Settings. */}
      <Route path="/voice-profiles" element={<WithLayout><VoiceProfilesPage /></WithLayout>} />
      <Route path="/voice-clone" element={<WithLayout><VoiceClonePage /></WithLayout>} />
      <Route path="/vod-downloader" element={<WithLayout><VodDownloaderPage /></WithLayout>} />
      <Route path="/vod-pipeline" element={<WithLayout><VodPipelinePage /></WithLayout>} />
      <Route path="/vod-pipeline/:vodId" element={<WithLayout><VodDetailPage /></WithLayout>} />
      <Route path="/transcription" element={<WithLayout><TranscriptionPage /></WithLayout>} />
      <Route path="/twitch-profiles" element={<WithLayout><TwitchProfilesPage /></WithLayout>} />

      {/* Old navigation paths now resolve to the consolidated areas. */}
      <Route path="/editor" element={<Navigate to="/projects" replace />} />
      <Route path="/layouts" element={<Navigate to="/projects" replace />} />
      <Route path="/vod-explorer" element={<Navigate to="/vod-downloader" replace />} />
      <Route path="/clips" element={<Navigate to="/vod-pipeline" replace />} />
      <Route path="/ideas" element={<Navigate to="/create" replace />} />
      <Route path="/recording-studio" element={<Navigate to="/voice-profiles" replace />} />
      <Route path="/synthetic-studio" element={<Navigate to="/voice-clone" replace />} />
      <Route path="/automations" element={<Navigate to="/jobs" replace />} />
      <Route path="/publishing" element={<Navigate to="/projects" replace />} />

      <Route path="*" element={<WithLayout><NotFoundPage /></WithLayout>} />
    </Routes>
  );
}
