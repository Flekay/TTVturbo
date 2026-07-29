import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Film,
  Pause,
  Play,
  Plus,
  Scissors,
  SkipBack,
  Trash2,
  Unlink,
  Video,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { EditSequence, TimelineClip, TimelineTrack } from "../../features/projects/api";
import { Button } from "../ui/Button";

interface EditorTimelineProps {
  sequence: EditSequence;
  tracks: TimelineTrack[];
  playheadUs: number;
  playing: boolean;
  selectedTrackId: string | null;
  selectedClipId: string | null;
  mediaTitles: Record<string, string>;
  onPlayToggle: () => void;
  onSeek: (timeUs: number) => void;
  onAddMedia: () => void;
  onSelect: (trackId: string, clipId: string) => void;
  onTrackSelect: (trackId: string) => void;
  onSplit: (trackId: string, clip: TimelineClip) => void;
  onSeparateAudio: (trackId: string, clip: TimelineClip) => void;
  onDuplicate: (trackId: string, clip: TimelineClip) => void;
  onDeleteClip: (trackId: string, clip: TimelineClip) => void;
  onToggleMute: (trackId: string, clip: TimelineClip) => void;
  onMoveClip: (trackId: string, clip: TimelineClip, targetTrackId: string, timelineStartUs: number) => void;
  onTrimClip: (trackId: string, clip: TimelineClip, sourceStartUs: number, sourceEndUs: number, timelineStartUs: number) => void;
  onAddTrack: (type: "VIDEO" | "AUDIO") => void;
  onDeleteTrack: (track: TimelineTrack) => void;
}

type ContextMenuState =
  | { kind: "clip"; x: number; y: number; track: TimelineTrack; clip: TimelineClip }
  | { kind: "lane"; x: number; y: number; track: TimelineTrack; timeUs: number }
  | null;

interface DragState {
  key: string;
  deltaUs: number;
  targetTrackId: string;
}

interface TrimState {
  key: string;
  edge: "left" | "right";
  sourceStartUs: number;
  sourceEndUs: number;
  timelineStartUs: number;
}

function durationUs(clip: TimelineClip): number {
  return Math.max(1, (clip.source_end_us - clip.source_start_us) / Math.max(0.05, Number(clip.speed ?? 1)));
}

function formatTimecode(timeUs: number, fps: number): string {
  const totalSeconds = Math.max(0, timeUs / 1_000_000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const frames = Math.floor((totalSeconds - Math.floor(totalSeconds)) * fps);
  return [hours, minutes, seconds, frames].map((value) => String(value).padStart(2, "0")).join(":");
}

function rulerLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function snapUs(value: number): number {
  return Math.max(0, Math.round(value / 100_000) * 100_000);
}

function trackAccepts(track: TimelineTrack, sourceTrack: TimelineTrack): boolean {
  if (sourceTrack.type === "AUDIO") return track.type === "AUDIO";
  if (["VIDEO", "GAMEPLAY", "FACECAM", "OVERLAY"].includes(sourceTrack.type)) {
    return ["VIDEO", "GAMEPLAY", "FACECAM", "OVERLAY"].includes(track.type);
  }
  return track.type === sourceTrack.type;
}

export function EditorTimeline({
  sequence,
  tracks,
  playheadUs,
  playing,
  selectedTrackId,
  selectedClipId,
  mediaTitles,
  onPlayToggle,
  onSeek,
  onAddMedia,
  onSelect,
  onTrackSelect,
  onSplit,
  onSeparateAudio,
  onDuplicate,
  onDeleteClip,
  onToggleMute,
  onMoveClip,
  onTrimClip,
  onAddTrack,
  onDeleteTrack,
}: EditorTimelineProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const headersRef = useRef<HTMLDivElement | null>(null);
  const [pixelsPerSecond, setPixelsPerSecond] = useState(72);
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [trim, setTrim] = useState<TrimState | null>(null);
  const fps = sequence.fps_numerator / sequence.fps_denominator;

  const contentDurationUs = useMemo(() => {
    let end = 60_000_000;
    for (const track of tracks) {
      for (const clip of Object.values(track.clips ?? {})) {
        end = Math.max(end, clip.timeline_start_us + durationUs(clip) + 5_000_000);
      }
    }
    return end;
  }, [tracks]);
  const contentWidth = Math.max(900, contentDurationUs / 1_000_000 * pixelsPerSecond);
  const tickSeconds = pixelsPerSecond >= 110 ? 1 : pixelsPerSecond >= 55 ? 5 : pixelsPerSecond >= 28 ? 10 : 30;
  const ticks = useMemo(() => {
    const count = Math.ceil(contentDurationUs / 1_000_000 / tickSeconds);
    return Array.from({ length: count + 1 }, (_, index) => index * tickSeconds);
  }, [contentDurationUs, tickSeconds]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", key); };
  }, [contextMenu]);

  function timeFromLanePoint(element: HTMLElement, clientX: number): number {
    const rect = element.getBoundingClientRect();
    return snapUs((clientX - rect.left) / pixelsPerSecond * 1_000_000);
  }

  function timeFromLaneEvent(event: ReactPointerEvent<HTMLElement>): number {
    return timeFromLanePoint(event.currentTarget, event.clientX);
  }

  function contextPosition(clientX: number, clientY: number): { x: number; y: number } {
    return {
      x: Math.max(8, Math.min(clientX, window.innerWidth - 236)),
      y: Math.max(8, Math.min(clientY, window.innerHeight - 230)),
    };
  }

  function beginMove(event: ReactPointerEvent<HTMLDivElement>, track: TimelineTrack, clip: TimelineClip) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(track.id, clip.id);
    const startX = event.clientX;
    let latestDelta = 0;
    let targetTrackId = track.id;
    const key = `${track.id}:${clip.id}`;

    const update = (pointer: PointerEvent) => {
      latestDelta = (pointer.clientX - startX) / pixelsPerSecond * 1_000_000;
      const element = document.elementFromPoint(pointer.clientX, pointer.clientY)?.closest<HTMLElement>("[data-track-id]");
      const candidate = tracks.find((item) => item.id === element?.dataset.trackId);
      if (candidate && trackAccepts(candidate, track)) targetTrackId = candidate.id;
      setDrag({ key, deltaUs: latestDelta, targetTrackId });
    };
    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setDrag(null);
      const newStart = snapUs(clip.timeline_start_us + latestDelta);
      if (newStart !== clip.timeline_start_us || targetTrackId !== track.id) onMoveClip(track.id, clip, targetTrackId, newStart);
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function beginTrim(event: ReactPointerEvent<HTMLButtonElement>, track: TimelineTrack, clip: TimelineClip, edge: "left" | "right") {
    event.preventDefault();
    event.stopPropagation();
    onSelect(track.id, clip.id);
    const startX = event.clientX;
    const originalStart = clip.source_start_us;
    const originalEnd = clip.source_end_us;
    const originalTimeline = clip.timeline_start_us;
    let latest: TrimState = { key: `${track.id}:${clip.id}`, edge, sourceStartUs: originalStart, sourceEndUs: originalEnd, timelineStartUs: originalTimeline };

    const update = (pointer: PointerEvent) => {
      const deltaTimelineUs = (pointer.clientX - startX) / pixelsPerSecond * 1_000_000;
      const speed = Math.max(0.05, Number(clip.speed ?? 1));
      const sourceDeltaUs = deltaTimelineUs * speed;
      if (edge === "left") {
        const nextSourceStart = Math.min(originalEnd - 10_000, Math.max(0, originalStart + sourceDeltaUs));
        const actualSourceDelta = nextSourceStart - originalStart;
        latest = {
          key: `${track.id}:${clip.id}`,
          edge,
          sourceStartUs: Math.round(nextSourceStart),
          sourceEndUs: originalEnd,
          timelineStartUs: snapUs(originalTimeline + actualSourceDelta / speed),
        };
      } else {
        latest = {
          key: `${track.id}:${clip.id}`,
          edge,
          sourceStartUs: originalStart,
          sourceEndUs: Math.max(originalStart + 10_000, Math.round(originalEnd + sourceDeltaUs)),
          timelineStartUs: originalTimeline,
        };
      }
      setTrim(latest);
    };
    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setTrim(null);
      if (latest.sourceStartUs !== originalStart || latest.sourceEndUs !== originalEnd || latest.timelineStartUs !== originalTimeline) {
        onTrimClip(track.id, clip, latest.sourceStartUs, latest.sourceEndUs, latest.timelineStartUs);
      }
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function closeAnd(action: () => void) {
    setContextMenu(null);
    action();
  }

  const selectedTrack = tracks.find((track) => track.id === selectedTrackId);
  const selectedClip = selectedTrack?.clips?.[selectedClipId ?? ""];
  const selectedCanSplit = Boolean(selectedClip && playheadUs > selectedClip.timeline_start_us && playheadUs < selectedClip.timeline_start_us + durationUs(selectedClip));

  return (
    <section className="editor-timeline-shell">
      <div className="editor-transport">
        <div className="editor-transport__left">
          <Button variant="icon" size="sm" aria-label="Zum Anfang" onClick={() => onSeek(0)}><SkipBack size={16} /></Button>
          <Button variant="icon" size="sm" aria-label="Ein Bild zurück" onClick={() => onSeek(Math.max(0, playheadUs - 1_000_000 / fps))}><ChevronLeft size={16} /></Button>
          <Button variant="primary" size="sm" className="editor-play-button" aria-label={playing ? "Pause" : "Abspielen"} onClick={onPlayToggle}>{playing ? <Pause size={17} /> : <Play size={17} />}</Button>
          <Button variant="icon" size="sm" aria-label="Ein Bild vor" onClick={() => onSeek(playheadUs + 1_000_000 / fps)}><ChevronRight size={16} /></Button>
          <output className="editor-timecode">{formatTimecode(playheadUs, fps)}</output>
          <Button variant="icon" size="sm" aria-label="Ausgewählten Clip teilen" disabled={!selectedCanSplit || !selectedTrack || !selectedClip} onClick={() => { if (selectedTrack && selectedClip) onSplit(selectedTrack.id, selectedClip); }}><Scissors size={15} /></Button>
        </div>
        <div className="editor-transport__right">
          <ZoomOut size={14} />
          <input aria-label="Timeline-Zoom" type="range" min={24} max={180} step={6} value={pixelsPerSecond} onChange={(event) => setPixelsPerSecond(Number(event.target.value))} />
          <ZoomIn size={14} />
          <Button variant="primary" size="sm" onClick={onAddMedia}><Plus size={15} /> Medien hinzufügen</Button>
        </div>
      </div>

      <div className="editor-timeline-body">
        <div ref={headersRef} className="editor-track-headers">
          <div className="editor-ruler-corner">Spuren</div>
          {tracks.map((track) => (
            <div key={track.id} className="editor-track-header">
              <span className="editor-track-header__icon">{track.type === "AUDIO" ? <AudioLines size={15} /> : <Video size={15} />}</span>
              <span className="editor-track-header__name"><strong>{track.name || track.type}</strong><small>{Object.keys(track.clips ?? {}).length} Clips</small></span>
              <Button variant="icon" size="sm" aria-label="Spur löschen" onClick={() => onDeleteTrack(track)}><Trash2 size={13} /></Button>
            </div>
          ))}
          {tracks.length === 0 ? <div className="editor-track-header editor-track-header--empty">Noch keine Spuren</div> : null}
          <div className="editor-track-add">
            <button type="button" onClick={() => onAddTrack("VIDEO")}><Plus size={13} /> Videospur</button>
            <button type="button" onClick={() => onAddTrack("AUDIO")}><Plus size={13} /> Audiospur</button>
          </div>
        </div>

        <div ref={viewportRef} className="editor-timeline-viewport" onScroll={(event) => { if (headersRef.current) headersRef.current.scrollTop = event.currentTarget.scrollTop; }}>
          <div className="editor-timeline-content" style={{ width: contentWidth }}>
            <div className="editor-ruler" onPointerDown={(event) => onSeek(timeFromLaneEvent(event))}>
              {ticks.map((seconds) => <span key={seconds} style={{ left: seconds * pixelsPerSecond }}><i />{rulerLabel(seconds)}</span>)}
            </div>
            <div className="editor-playhead" style={{ left: playheadUs / 1_000_000 * pixelsPerSecond }}><span /></div>

            {tracks.map((track) => (
              <div
                key={track.id}
                className={`editor-track-lane${drag?.targetTrackId === track.id ? " is-drop-target" : ""}`}
                data-track-id={track.id}
                onPointerDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  onTrackSelect(track.id);
                  onSeek(timeFromLaneEvent(event));
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  onTrackSelect(track.id);
                  const position = contextPosition(event.clientX, event.clientY);
                  setContextMenu({ kind: "lane", ...position, track, timeUs: timeFromLanePoint(event.currentTarget, event.clientX) });
                }}
              >
                {Object.values(track.clips ?? {}).map((clip) => {
                  const key = `${track.id}:${clip.id}`;
                  const trimValue = trim?.key === key ? trim : null;
                  const sourceStart = trimValue?.sourceStartUs ?? clip.source_start_us;
                  const sourceEnd = trimValue?.sourceEndUs ?? clip.source_end_us;
                  const timelineStart = trimValue?.timelineStartUs ?? clip.timeline_start_us;
                  const baseDuration = (sourceEnd - sourceStart) / Math.max(0.05, Number(clip.speed ?? 1));
                  const dragDelta = drag?.key === key ? drag.deltaUs : 0;
                  const left = Math.max(0, (timelineStart + dragDelta) / 1_000_000 * pixelsPerSecond);
                  const width = Math.max(22, baseDuration / 1_000_000 * pixelsPerSecond);
                  const selected = track.id === selectedTrackId && clip.id === selectedClipId;
                  return (
                    <div
                      key={clip.id}
                      className={`editor-timeline-clip editor-timeline-clip--${track.type.toLowerCase()}${selected ? " is-selected" : ""}${drag?.key === key ? " is-dragging" : ""}`}
                      style={{ left, width }}
                      onPointerDown={(event) => beginMove(event, track, clip)}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onSelect(track.id, clip.id);
                        setContextMenu({ kind: "clip", ...contextPosition(event.clientX, event.clientY), track, clip });
                      }}
                    >
                      <button className="editor-timeline-clip__trim editor-timeline-clip__trim--left" type="button" aria-label="Clip-Anfang trimmen" onPointerDown={(event) => beginTrim(event, track, clip, "left")} />
                      <span className="editor-timeline-clip__content">
                        {track.type === "AUDIO" ? <AudioLines size={13} /> : <Film size={13} />}
                        <strong>{mediaTitles[clip.source_media_item_id] ?? "Clip"}</strong>
                        {Boolean(clip.audio_muted) ? <VolumeX size={12} /> : null}
                      </span>
                      <button className="editor-timeline-clip__trim editor-timeline-clip__trim--right" type="button" aria-label="Clip-Ende trimmen" onPointerDown={(event) => beginTrim(event, track, clip, "right")} />
                    </div>
                  );
                })}
              </div>
            ))}

            {tracks.length === 0 ? (
              <button type="button" className="editor-timeline-empty" onClick={onAddMedia}><Plus size={18} /> Erstes Medium hinzufügen</button>
            ) : null}
          </div>
        </div>
      </div>

      {contextMenu ? (
        <div className="editor-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
          {contextMenu.kind === "clip" ? (
            <>
              <button type="button" disabled={!(playheadUs > contextMenu.clip.timeline_start_us && playheadUs < contextMenu.clip.timeline_start_us + durationUs(contextMenu.clip))} onClick={() => closeAnd(() => onSplit(contextMenu.track.id, contextMenu.clip))}><Scissors size={14} /> Am Abspielkopf teilen</button>
              {contextMenu.track.type !== "AUDIO" ? <button type="button" onClick={() => closeAnd(() => onSeparateAudio(contextMenu.track.id, contextMenu.clip))}><Unlink size={14} /> Video und Audio trennen</button> : null}
              <button type="button" onClick={() => closeAnd(() => onToggleMute(contextMenu.track.id, contextMenu.clip))}>{Boolean(contextMenu.clip.audio_muted) ? <Volume2 size={14} /> : <VolumeX size={14} />}{Boolean(contextMenu.clip.audio_muted) ? " Ton einschalten" : " Stummschalten"}</button>
              <button type="button" onClick={() => closeAnd(() => onDuplicate(contextMenu.track.id, contextMenu.clip))}><Plus size={14} /> Duplizieren</button>
              <span className="editor-context-menu__separator" />
              <button type="button" className="is-danger" onClick={() => closeAnd(() => onDeleteClip(contextMenu.track.id, contextMenu.clip))}><Trash2 size={14} /> Clip entfernen</button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => closeAnd(() => { onTrackSelect(contextMenu.track.id); onSeek(contextMenu.timeUs); onAddMedia(); })}><Plus size={14} /> Medien hier hinzufügen</button>
              <button type="button" onClick={() => closeAnd(() => onAddTrack("VIDEO"))}><Video size={14} /> Videospur hinzufügen</button>
              <button type="button" onClick={() => closeAnd(() => onAddTrack("AUDIO"))}><AudioLines size={14} /> Audiospur hinzufügen</button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
