import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { AppLayout } from "./components/layout/AppLayout";
import { DashboardPage } from "./pages/DashboardPage";
import { VoiceProfilesPage } from "./pages/VoiceProfilesPage";
import { VoiceClonePage } from "./pages/VoiceClonePage";
import { VodPipelinePage } from "./pages/VodPipelinePage";
import { TwitchProfilesPage } from "./pages/TwitchProfilesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { UnavailablePage } from "./pages/UnavailablePage";
import { findRouteMeta } from "./router.routes";

function useDocumentTitle() {
  const location = useLocation();
  useEffect(() => {
    const meta = findRouteMeta(location.pathname);
    document.title = meta ? `${meta.label} – TTVturbo` : "TTVturbo";
  }, [location.pathname]);
}

function Unavailable({
  title,
  description,
  plannedFeatures,
}: {
  title: string;
  description: string;
  plannedFeatures: string[];
}) {
  return (
    <UnavailablePage title={title} description={description} plannedFeatures={plannedFeatures} />
  );
}

export function AppRouter() {
  useDocumentTitle();
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <AppLayout>
            <DashboardPage />
          </AppLayout>
        }
      />
      <Route
        path="/voice-profiles"
        element={
          <AppLayout>
            <VoiceProfilesPage />
          </AppLayout>
        }
      />
      <Route
        path="/voice-clone"
        element={
          <AppLayout>
            <VoiceClonePage />
          </AppLayout>
        }
      />
      <Route
        path="/vod-pipeline"
        element={
          <AppLayout>
            <VodPipelinePage />
          </AppLayout>
        }
      />
      <Route
        path="/twitch-profiles"
        element={
          <AppLayout>
            <TwitchProfilesPage />
          </AppLayout>
        }
      />
      {/* Legacy redirect: the old placeholder route now lives at /vod-pipeline. */}
      <Route path="/vod-explorer" element={<Navigate to="/vod-pipeline" replace />} />
      <Route
        path="/clips"
        element={
          <AppLayout>
            <Unavailable
              title="Clip-Vorschläge"
              description="Hier werden später automatisch generierte Clip-Vorschläge aus VODs angezeigt."
              plannedFeatures={[
                "Automatische Clip-Erkennung",
                "Vorschau und Auswahl",
                "Übernahme in den Video Editor",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/ideas"
        element={
          <AppLayout>
            <Unavailable
              title="Ideen"
              description="Hier werden später Gesprächsthemen und Inhaltsideen gesammelt und verwaltet."
              plannedFeatures={[
                "Ideen erfassen und kategorisieren",
                "Verknüpfung mit Aufnahmen und Clips",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/recording-studio"
        element={
          <AppLayout>
            <Unavailable
              title="Aufnahmestudio"
              description="Hier entsteht später eine strukturierte Umgebung für längere Aufnahmesitzungen."
              plannedFeatures={[
                "Strukturierte Aufnahmesitzungen",
                "Skript- und Szenenverwaltung",
                "Direkte Übergabe an Synthetic Studio",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/synthetic-studio"
        element={
          <AppLayout>
            <Unavailable
              title="Synthetic Studio"
              description="Hier werden später Voice-Clones erzeugt und synthetische Aufnahmen erstellt."
              plannedFeatures={[
                "Voice-Clone aus Referenzaufnahmen",
                "Synthetische Aufnahmen mit Qwen3-TTS",
                "Qualitätskontrolle und Export",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/editor"
        element={
          <AppLayout>
            <Unavailable
              title="Video Editor"
              description="Hier entsteht später ein Editor zum Zuschneiden und Arrangieren von Videos."
              plannedFeatures={[
                "Videos zuschneiden und zusammenfügen",
                "Szenen und Spuren verwalten",
                "Export in verschiedene Formate",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/layouts"
        element={
          <AppLayout>
            <Unavailable
              title="Layout Studio"
              description="Hier werden später Szenen und Layouts für die Produktion vorbereitet."
              plannedFeatures={[
                "Szenen und Layouts verwalten",
                "Vorlagen für wiederkehrende Formate",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/automations"
        element={
          <AppLayout>
            <Unavailable
              title="Automationen"
              description="Hier werden später wiederkehrende Abläufe automatisiert konfiguriert."
              plannedFeatures={[
                "Wiederkehrende Abläufe definieren",
                "Trigger und Aktionen verknüpfen",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/publishing"
        element={
          <AppLayout>
            <Unavailable
              title="Veröffentlichungen"
              description="Hier werden später fertige Clips für die Veröffentlichung vorbereitet."
              plannedFeatures={[
                "Veröffentlichungsziele verwalten",
                "Exports vorbereiten und ausliefern",
              ]}
            />
          </AppLayout>
        }
      />
      <Route
        path="/settings"
        element={
          <AppLayout>
            <SettingsPage />
          </AppLayout>
        }
      />
      <Route
        path="*"
        element={
          <AppLayout>
            <NotFoundPage />
          </AppLayout>
        }
      />
    </Routes>
  );
}
