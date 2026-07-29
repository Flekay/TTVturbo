import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AudioLines,
  Captions,
  ChevronRight,
  Film,
  FolderKanban,
  ImageOff,
  Mic2,
  Scissors,
  Sparkles,
  Video,
  WandSparkles,
} from "lucide-react";
import { useCapabilityStatus } from "../features/capabilities/hooks";
import type { QuickToolId } from "../features/capabilities/api";

interface ToolCardProps {
  to: string;
  icon: ReactNode;
  title: string;
  description: string;
  statusTool?: QuickToolId;
}

function ToolCard({ to, icon, title, description, statusTool }: ToolCardProps) {
  const status = useCapabilityStatus(statusTool ?? "video-upscale", Boolean(statusTool));
  const unavailable = statusTool ? status.data && !status.data.available : false;
  return (
    <Link className={`create-card${unavailable ? " create-card--unavailable" : ""}`} to={to}>
      <div className="create-card__icon">{icon}</div>
      <div className="create-card__body">
        <strong>{title}</strong>
        <span>{description}</span>
        {unavailable && <small>{status.data?.reasons?.[0] || "Backend nicht verfügbar"}</small>}
      </div>
      <ChevronRight size={18} />
    </Link>
  );
}

export function CreatePage() {
  return (
    <div className="page create-page">
      <section className="create-hero">
        <div>
          <span className="eyebrow">Create</span>
          <h1>Was möchtest du machen?</h1>
          <p>Schnellwerkzeuge arbeiten ohne Projekt und speichern nichts dauerhaft, bis du es ausdrücklich übernimmst.</p>
        </div>
      </section>

      <section className="create-section">
        <div className="section-heading">
          <div><h2>Schnellwerkzeuge</h2><p>Eine Datei oder einen Prompt verarbeiten, Ergebnis herunterladen, fertig.</p></div>
        </div>
        <div className="create-grid create-grid--tools">
          <ToolCard to="/create/video-upscale" icon={<Sparkles size={21} />} title="Video hochskalieren" description="2×, 4× oder benutzerdefinierte Auflösung." statusTool="video-upscale" />
          <ToolCard to="/create/background-removal" icon={<ImageOff size={21} />} title="Hintergrund entfernen" description="Transparent, weichgezeichnet oder neue Farbe." statusTool="video-background-removal" />
          <ToolCard to="/create/text-edit" icon={<WandSparkles size={21} />} title="Video per Text bearbeiten" description="Objekte, Stil oder Bildbereiche verändern." statusTool="video-text-edit" />
          <ToolCard to="/create/video-generation" icon={<Video size={21} />} title="Video generieren" description="Text-to-Video oder Image-to-Video." statusTool="video-generation" />
          <ToolCard to="/transcription" icon={<Captions size={21} />} title="Audio transkribieren" description="Datei hochladen und Transkript exportieren." />
          <ToolCard to="/voice-clone" icon={<AudioLines size={21} />} title="Voiceover erstellen" description="Text mit einem vorhandenen Voice-Profil erzeugen." />
        </div>
      </section>

      <section className="create-section">
        <div className="section-heading">
          <div><h2>Workflows</h2><p>Mehrere Schritte mit dauerhaftem Zustand, Review oder Timeline.</p></div>
        </div>
        <div className="create-grid create-grid--workflows">
          <ToolCard to="/vod-pipeline" icon={<Scissors size={21} />} title="Clip aus Stream oder VOD" description="VOD auswählen, transkribieren, analysieren und Clips vorbereiten." />
          <ToolCard to="/projects" icon={<FolderKanban size={21} />} title="Video bearbeiten" description="Projekt, Ausgaben, Timeline, Versionen und Render." />
          <ToolCard to="/vod-downloader" icon={<Film size={21} />} title="Twitch-VOD importieren" description="VODs synchronisieren und dauerhaft in die Library übernehmen." />
          <ToolCard to="/voice-profiles" icon={<Mic2 size={21} />} title="Voice-Profil aufnehmen" description="Referenzaufnahmen erstellen, prüfen und verwalten." />
        </div>
      </section>
    </div>
  );
}
