import { Film, Move, RotateCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { EditSequence, TimelineClip, TimelineTrack } from "../../features/projects/api";
import { libraryItemFileUrl } from "../../features/library/api";

export interface CanvasTransform {
  x: number;
  y: number;
  scale_x: number;
  scale_y: number;
  rotation: number;
}

interface EditorCanvasProps {
  sequence: EditSequence;
  tracks: TimelineTrack[];
  playheadUs: number;
  playing: boolean;
  selectedTrackId: string | null;
  selectedClipId: string | null;
  mediaTitles: Record<string, string>;
  onSelect: (trackId: string, clipId: string) => void;
  onTransformCommit: (trackId: string, clipId: string, transform: CanvasTransform) => Promise<void> | void;
  onAddMedia: () => void;
}

interface ActiveClip {
  key: string;
  track: TimelineTrack;
  clip: TimelineClip;
  visual: boolean;
}

type InteractionMode = "move" | "resize-nw" | "resize-ne" | "resize-sw" | "resize-se" | "rotate";

const VISUAL_TRACKS = new Set(["VIDEO", "GAMEPLAY", "FACECAM", "OVERLAY"]);
const AUDIO_TRACKS = new Set(["VIDEO", "GAMEPLAY", "FACECAM", "AUDIO"]);

function clipDurationUs(clip: TimelineClip): number {
  return Math.max(1, (clip.source_end_us - clip.source_start_us) / Math.max(0.05, Number(clip.speed ?? 1)));
}

function transformOf(clip: TimelineClip): CanvasTransform {
  return {
    x: Number(clip.transform?.x ?? 0),
    y: Number(clip.transform?.y ?? 0),
    scale_x: Number(clip.transform?.scale_x ?? 1),
    scale_y: Number(clip.transform?.scale_y ?? 1),
    rotation: Number(clip.transform?.rotation ?? 0),
  };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function EditorCanvas({
  sequence,
  tracks,
  playheadUs,
  playing,
  selectedTrackId,
  selectedClipId,
  mediaTitles,
  onSelect,
  onTransformCommit,
  onAddMedia,
}: EditorCanvasProps) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const mediaRefs = useRef(new Map<string, HTMLMediaElement>());
  const [drafts, setDrafts] = useState<Record<string, CanvasTransform>>({});

  const active = useMemo<ActiveClip[]>(() => {
    const entries: ActiveClip[] = [];
    for (const track of tracks) {
      for (const clip of Object.values(track.clips ?? {})) {
        const end = clip.timeline_start_us + clipDurationUs(clip);
        if (playheadUs < clip.timeline_start_us || playheadUs >= end) continue;
        const visual = VISUAL_TRACKS.has(track.type);
        if (!visual && !AUDIO_TRACKS.has(track.type)) continue;
        entries.push({ key: `${track.id}:${clip.id}`, track, clip, visual });
      }
    }
    return entries;
  }, [playheadUs, tracks]);

  useEffect(() => {
    const activeKeys = new Set(active.map((entry) => entry.key));
    for (const [key, element] of mediaRefs.current.entries()) {
      if (!activeKeys.has(key)) {
        element.pause();
        mediaRefs.current.delete(key);
      }
    }
    for (const entry of active) {
      const element = mediaRefs.current.get(entry.key);
      if (!element) continue;
      const speed = Math.max(0.05, Number(entry.clip.speed ?? 1));
      const target = entry.clip.source_start_us / 1_000_000 + ((playheadUs - entry.clip.timeline_start_us) / 1_000_000) * speed;
      element.playbackRate = speed;
      if (!Number.isFinite(element.currentTime) || Math.abs(element.currentTime - target) > (playing ? 0.22 : 0.035)) {
        try { element.currentTime = Math.max(0, target); } catch { /* metadata may not be ready yet */ }
      }
      if (playing) void element.play().catch(() => undefined);
      else element.pause();
    }
  }, [active, playheadUs, playing]);

  function beginInteraction(
    event: ReactPointerEvent<HTMLElement>,
    entry: ActiveClip,
    mode: InteractionMode,
  ) {
    event.preventDefault();
    event.stopPropagation();
    onSelect(entry.track.id, entry.clip.id);
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const start = drafts[entry.key] ?? transformOf(entry.clip);
    const startX = event.clientX;
    const startY = event.clientY;
    const centerX = rect.left + (start.x + start.scale_x / 2) * rect.width;
    const centerY = rect.top + (start.y + start.scale_y / 2) * rect.height;
    const startAngle = Math.atan2(event.clientY - centerY, event.clientX - centerX) * 180 / Math.PI;
    let latest = start;

    const update = (pointer: PointerEvent) => {
      const dx = (pointer.clientX - startX) / Math.max(1, rect.width);
      const dy = (pointer.clientY - startY) / Math.max(1, rect.height);
      let next = { ...start };
      if (mode === "move") {
        next.x = clamp(start.x + dx, -1.5, 1.5);
        next.y = clamp(start.y + dy, -1.5, 1.5);
      } else if (mode === "rotate") {
        const angle = Math.atan2(pointer.clientY - centerY, pointer.clientX - centerX) * 180 / Math.PI;
        next.rotation = Math.round((start.rotation + angle - startAngle) * 10) / 10;
      } else {
        const west = mode.endsWith("w");
        const east = mode.endsWith("e");
        const north = mode.includes("n");
        const south = mode.includes("s");
        if (west) {
          const width = Math.max(0.03, start.scale_x - dx);
          next.x = start.x + start.scale_x - width;
          next.scale_x = width;
        }
        if (east) next.scale_x = Math.max(0.03, start.scale_x + dx);
        if (north) {
          const height = Math.max(0.03, start.scale_y - dy);
          next.y = start.y + start.scale_y - height;
          next.scale_y = height;
        }
        if (south) next.scale_y = Math.max(0.03, start.scale_y + dy);
      }
      latest = next;
      setDrafts((current) => ({ ...current, [entry.key]: next }));
    };

    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      Promise.resolve(onTransformCommit(entry.track.id, entry.clip.id, latest)).finally(() => {
        setDrafts((current) => {
          const next = { ...current };
          delete next[entry.key];
          return next;
        });
      });
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  return (
    <section className="editor-stage-shell">
      <div className="editor-stage-viewport">
        <div
          ref={canvasRef}
          className="editor-stage"
          style={{ aspectRatio: `${sequence.width} / ${sequence.height}` }}
          onPointerDown={() => undefined}
        >
          <div className="editor-stage__safe-area" />
          {active.filter((entry) => entry.visual).map((entry, index) => {
            const selected = entry.track.id === selectedTrackId && entry.clip.id === selectedClipId;
            const value = drafts[entry.key] ?? transformOf(entry.clip);
            return (
              <div
                key={entry.key}
                className={`editor-stage-item${selected ? " is-selected" : ""}`}
                style={{
                  left: `${value.x * 100}%`,
                  top: `${value.y * 100}%`,
                  width: `${value.scale_x * 100}%`,
                  height: `${value.scale_y * 100}%`,
                  transform: `rotate(${value.rotation}deg)`,
                  opacity: Number(entry.clip.opacity ?? 1),
                  zIndex: index + 1,
                }}
                onPointerDown={(event) => beginInteraction(event, entry, "move")}
                onDoubleClick={() => onSelect(entry.track.id, entry.clip.id)}
              >
                <video
                  ref={(node) => { if (node) mediaRefs.current.set(entry.key, node); else mediaRefs.current.delete(entry.key); }}
                  src={libraryItemFileUrl(entry.clip.source_media_item_id)}
                  muted={Boolean(entry.clip.audio_muted)}
                  playsInline
                  preload="auto"
                  draggable={false}
                />
                {selected ? (
                  <>
                    <span className="editor-stage-item__label"><Move size={12} /> {mediaTitles[entry.clip.source_media_item_id] ?? "Clip"}</span>
                    <button type="button" className="editor-stage-handle editor-stage-handle--nw" aria-label="Oben links skalieren" onPointerDown={(event) => beginInteraction(event, entry, "resize-nw")} />
                    <button type="button" className="editor-stage-handle editor-stage-handle--ne" aria-label="Oben rechts skalieren" onPointerDown={(event) => beginInteraction(event, entry, "resize-ne")} />
                    <button type="button" className="editor-stage-handle editor-stage-handle--sw" aria-label="Unten links skalieren" onPointerDown={(event) => beginInteraction(event, entry, "resize-sw")} />
                    <button type="button" className="editor-stage-handle editor-stage-handle--se" aria-label="Unten rechts skalieren" onPointerDown={(event) => beginInteraction(event, entry, "resize-se")} />
                    <button type="button" className="editor-stage-rotate" aria-label="Rotieren" onPointerDown={(event) => beginInteraction(event, entry, "rotate")}><RotateCw size={12} /></button>
                  </>
                ) : null}
              </div>
            );
          })}

          {active.filter((entry) => !entry.visual).map((entry) => (
            <audio
              key={entry.key}
              ref={(node) => { if (node) mediaRefs.current.set(entry.key, node); else mediaRefs.current.delete(entry.key); }}
              src={libraryItemFileUrl(entry.clip.source_media_item_id)}
              muted={Boolean(entry.clip.audio_muted)}
              preload="auto"
            />
          ))}

          {active.filter((entry) => entry.visual).length === 0 ? (
            <button type="button" className="editor-stage-empty" onClick={onAddMedia}>
              <Film size={32} />
              <strong>Leere Szene</strong>
              <span>Medien hinzufügen oder an diese Position ziehen.</span>
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
