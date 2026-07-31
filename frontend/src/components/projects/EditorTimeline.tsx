import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Film,
  GripVertical,
  Image as ImageIcon,
  Layers3,
  Pause,
  Pencil,
  Play,
  Plus,
  Scissors,
  SkipBack,
  Sparkles,
  Trash2,
  Type,
  Unlink,
  Volume2,
  VolumeX,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type {
  EditSequence,
  TimelineClip,
  TimelineEffect,
  TimelineElementKind,
  TimelineTrack,
} from "../../features/projects/api";
import {
  clipDurationUs,
  effectByAnchor,
  elementKind,
  nearestAvailableStart,
  nextOccupiedStart,
  previousOccupiedEnd,
  snapTimelineUs,
} from "../../features/projects/timelineLogic";
import { Button } from "../ui/Button";

interface EditorTimelineProps {
  sequence: EditSequence;
  tracks: TimelineTrack[];
  playheadUs: number;
  playing: boolean;
  selectedTrackId: string | null;
  selectedClipId: string | null;
  mediaTitles: Record<string, string>;
  mediaFileTypes: Record<string, string>;
  onPlayToggle: () => void;
  onSeek: (timeUs: number) => void;
  onAddMedia: () => void;
  onAddText: (trackId?: string, timeUs?: number) => void;
  onEditText: (trackId: string, clip: TimelineClip) => void;
  onSelect: (trackId: string, clipId: string) => void;
  onTrackSelect: (trackId: string) => void;
  onSplit: (trackId: string, clip: TimelineClip) => void;
  onSeparateAudio: (trackId: string, clip: TimelineClip) => void;
  onDuplicate: (trackId: string, clip: TimelineClip) => void;
  onDeleteClip: (trackId: string, clip: TimelineClip) => void;
  onToggleMute: (trackId: string, clip: TimelineClip) => void;
  onMoveClip: (trackId: string, clip: TimelineClip, targetTrackId: string, timelineStartUs: number) => void;
  onTrimClip: (trackId: string, clip: TimelineClip, sourceStartUs: number, sourceEndUs: number, timelineStartUs: number) => void;
  onAddTrack: () => void;
  onDeleteTrack: (track: TimelineTrack) => void;
  onRenameTrack: (track: TimelineTrack, name: string) => Promise<void> | void;
  onReorderTracks: (trackIds: string[]) => Promise<void> | void;
  onAddEffect: (trackId: string, clip: TimelineClip, anchor: "START" | "END") => void;
  onUpdateEffect: (trackId: string, clip: TimelineClip, effect: TimelineEffect, durationUs: number) => void;
  onRemoveEffect: (trackId: string, clip: TimelineClip, effect: TimelineEffect) => void;
}

type ContextMenuState =
  | { kind: "clip"; x: number; y: number; track: TimelineTrack; clip: TimelineClip }
  | { kind: "lane"; x: number; y: number; track: TimelineTrack; timeUs: number }
  | null;

interface DragState {
  key: string;
  sourceTrackId: string;
  clipId: string;
  targetTrackId: string;
  resolvedStartUs: number;
}

interface TrimState {
  key: string;
  edge: "left" | "right";
  sourceStartUs: number;
  sourceEndUs: number;
  timelineStartUs: number;
}

interface EffectResizeState {
  key: string;
  durationUs: number;
}

interface LayerDragState {
  sourceId: string;
}

interface RenameLayerState {
  id: string;
  value: string;
  busy: boolean;
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

function KindIcon({ kind, size = 13 }: { kind: TimelineElementKind; size?: number }) {
  if (kind === "AUDIO") return <AudioLines size={size} />;
  if (kind === "IMAGE") return <ImageIcon size={size} />;
  if (kind === "TEXT") return <Type size={size} />;
  return <Film size={size} />;
}

function clipTitle(clip: TimelineClip, kind: TimelineElementKind, mediaTitles: Record<string, string>): string {
  if (kind === "TEXT") return clip.text?.content?.trim() || "Text";
  return mediaTitles[clip.source_media_item_id] ?? kind[0] + kind.slice(1).toLocaleLowerCase("de-DE");
}

function layerTitle(layer: TimelineTrack, index: number): string {
  const name = layer.name?.trim();
  if (!name) return `Layer ${index + 1}`;
  const legacyDefault = /^Spur(?:\s+(\d+))?$/i.exec(name);
  if (legacyDefault) return `Layer ${legacyDefault[1] ?? index + 1}`;
  return name;
}

function zoomBoundsFor(viewportWidth: number, tracks: TimelineTrack[], contentDurationUs: number) {
  const usableWidth = Math.max(200, viewportWidth - 32);
  const contentSeconds = contentDurationUs / 1_000_000;
  const fitAll = usableWidth / Math.max(1, contentSeconds);
  let shortestClipSeconds = Infinity;
  for (const track of tracks) {
    for (const clip of Object.values(track.clips ?? {})) {
      shortestClipSeconds = Math.min(shortestClipSeconds, clipDurationUs(clip) / 1_000_000);
    }
  }
  const min = Math.max(4, Math.min(fitAll, 400));
  const max = Number.isFinite(shortestClipSeconds) && shortestClipSeconds > 0
    ? Math.max(min + 1, Math.min(240 / shortestClipSeconds, 4000))
    : Math.max(min + 1, Math.min(usableWidth / Math.max(0.5, contentSeconds / 8), 4000));
  return { minPps: min, maxPps: max, zoomStep: Math.max(1, Math.round((max - min) / 200)) };
}

function sameOrder(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export function EditorTimeline({
  sequence,
  tracks,
  playheadUs,
  playing,
  selectedTrackId,
  selectedClipId,
  mediaTitles,
  mediaFileTypes,
  onPlayToggle,
  onSeek,
  onAddMedia,
  onAddText,
  onEditText,
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
  onRenameTrack,
  onReorderTracks,
  onAddEffect,
  onUpdateEffect,
  onRemoveEffect,
}: EditorTimelineProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const headersRef = useRef<HTMLDivElement | null>(null);
  const viewportScrollRef = useRef({ left: 0, top: 0 });
  const renameCancelledRef = useRef(false);
  const [pixelsPerSecond, setPixelsPerSecond] = useState(72);
  const [zoomBounds, setZoomBounds] = useState({ minPps: 4, maxPps: 4000, zoomStep: 20 });
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [trim, setTrim] = useState<TrimState | null>(null);
  const [effectResize, setEffectResize] = useState<EffectResizeState | null>(null);
  const [layerDrag, setLayerDrag] = useState<LayerDragState | null>(null);
  const [localTrackOrder, setLocalTrackOrder] = useState<string[] | null>(null);
  const [renameLayer, setRenameLayer] = useState<RenameLayerState | null>(null);
  const fps = sequence.fps_numerator / sequence.fps_denominator;

  const actualContentDurationUs = useMemo(() => {
    let end = 60_000_000;
    for (const track of tracks) {
      for (const clip of Object.values(track.clips ?? {})) {
        end = Math.max(end, clip.timeline_start_us + clipDurationUs(clip) + 5_000_000);
      }
    }
    return end;
  }, [tracks]);

  const [contentDurationUs, setContentDurationUs] = useState(actualContentDurationUs);
  const extentSequenceRef = useRef(sequence.id);

  useEffect(() => {
    if (extentSequenceRef.current !== sequence.id) {
      extentSequenceRef.current = sequence.id;
      setContentDurationUs(actualContentDurationUs);
      return;
    }
    setContentDurationUs((current) => {
      return Math.max(current, actualContentDurationUs);
    });
  }, [actualContentDurationUs, sequence.id]);

  // The persisted order is bottom-to-top because the renderer composites later
  // layers above earlier ones. Display it top-to-bottom like Photoshop.
  const baseTrackOrder = useMemo(() => tracks.map((track) => track.id).reverse(), [tracks]);
  useEffect(() => {
    if (!localTrackOrder) return;
    if (sameOrder(localTrackOrder, baseTrackOrder)) {
      setLocalTrackOrder(null);
      return;
    }
    if (localTrackOrder.length !== baseTrackOrder.length || localTrackOrder.some((id) => !baseTrackOrder.includes(id))) {
      setLocalTrackOrder(null);
    }
  }, [baseTrackOrder, localTrackOrder]);

  const displayTracks = useMemo(() => {
    const byId = new Map(tracks.map((track) => [track.id, track]));
    const order = localTrackOrder ?? baseTrackOrder;
    return order.map((id) => byId.get(id)).filter((track): track is TimelineTrack => Boolean(track));
  }, [baseTrackOrder, localTrackOrder, tracks]);

  const tracksRef = useRef(displayTracks);
  tracksRef.current = displayTracks;
  const contentDurationRef = useRef(contentDurationUs);
  contentDurationRef.current = contentDurationUs;

  useEffect(() => {
    const viewportEl = viewportRef.current;
    if (!viewportEl) return;
    const update = () => {
      const rect = viewportEl.getBoundingClientRect();
      if (rect.width <= 0) return;
      const nextBounds = zoomBoundsFor(rect.width, tracksRef.current, contentDurationRef.current);
      setZoomBounds(nextBounds);
      setPixelsPerSecond((current) => Math.max(nextBounds.minPps, Math.min(nextBounds.maxPps, current)));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(viewportEl);
    return () => observer.disconnect();
  }, []);

  const contentSeconds = contentDurationUs / 1_000_000;
  const { minPps, maxPps, zoomStep } = zoomBounds;

  const contentWidth = Math.max(900, contentSeconds * pixelsPerSecond);
  const tickSeconds = useMemo(() => {
    const target = 80 / pixelsPerSecond;
    const niceSteps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];
    return niceSteps.find((step) => step >= target) ?? niceSteps.at(-1)!;
  }, [pixelsPerSecond]);
  const ticks = useMemo(() => {
    const count = Math.ceil(contentSeconds / tickSeconds);
    return Array.from({ length: count + 1 }, (_, index) => index * tickSeconds);
  }, [contentSeconds, tickSeconds]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollLeft = viewportScrollRef.current.left;
    viewport.scrollTop = viewportScrollRef.current.top;
    if (headersRef.current) headersRef.current.scrollTop = viewportScrollRef.current.top;
  }, [contentWidth, tracks]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", key);
    };
  }, [contextMenu]);

  function timeFromElementPoint(element: HTMLElement, clientX: number): number {
    const rect = element.getBoundingClientRect();
    return snapTimelineUs((clientX - rect.left) / pixelsPerSecond * 1_000_000);
  }

  function contextPosition(clientX: number, clientY: number): { x: number; y: number } {
    return {
      x: Math.max(8, Math.min(clientX, window.innerWidth - 250)),
      y: Math.max(8, Math.min(clientY, window.innerHeight - 370)),
    };
  }

  function beginPlayheadDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const ruler = event.currentTarget;
    const update = (pointer: { clientX: number }) => onSeek(timeFromElementPoint(ruler, pointer.clientX));
    update(event);
    const move = (pointer: PointerEvent) => update(pointer);
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function beginMove(event: ReactPointerEvent<HTMLDivElement>, track: TimelineTrack, clip: TimelineClip) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(track.id, clip.id);
    const startX = event.clientX;
    const durationUs = clipDurationUs(clip);
    let targetTrackId = track.id;
    let resolvedStartUs = clip.timeline_start_us;
    const key = `${track.id}:${clip.id}`;

    const update = (pointer: PointerEvent) => {
      const element = document.elementFromPoint(pointer.clientX, pointer.clientY)?.closest<HTMLElement>("[data-track-id]");
      const candidate = displayTracks.find((item) => item.id === element?.dataset.trackId);
      if (candidate) targetTrackId = candidate.id;
      const deltaUs = (pointer.clientX - startX) / pixelsPerSecond * 1_000_000;
      const proposed = snapTimelineUs(clip.timeline_start_us + deltaUs);
      const targetTrack = displayTracks.find((item) => item.id === targetTrackId) ?? track;
      resolvedStartUs = nearestAvailableStart(targetTrack, proposed, durationUs, targetTrack.id === track.id ? clip.id : undefined);
      setDrag({ key, sourceTrackId: track.id, clipId: clip.id, targetTrackId, resolvedStartUs });
    };
    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setDrag(null);
      if (resolvedStartUs !== clip.timeline_start_us || targetTrackId !== track.id) {
        onMoveClip(track.id, clip, targetTrackId, resolvedStartUs);
      }
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function beginLayerReorder(event: ReactPointerEvent<HTMLButtonElement>, track: TimelineTrack) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const originalOrder = displayTracks.map((item) => item.id);
    let currentOrder = [...originalOrder];
    setLocalTrackOrder(currentOrder);
    setLayerDrag({ sourceId: track.id });

    const update = (pointer: PointerEvent) => {
      const targetHeader = document
        .elementFromPoint(pointer.clientX, pointer.clientY)
        ?.closest<HTMLElement>("[data-layer-header-id]");
      const targetId = targetHeader?.dataset.layerHeaderId;
      if (!targetId || targetId === track.id || !currentOrder.includes(targetId)) return;
      const targetRect = targetHeader.getBoundingClientRect();
      const afterTarget = pointer.clientY >= targetRect.top + targetRect.height / 2;
      const withoutSource = currentOrder.filter((id) => id !== track.id);
      let insertAt = withoutSource.indexOf(targetId);
      if (insertAt < 0) return;
      if (afterTarget) insertAt += 1;
      const nextOrder = [...withoutSource];
      nextOrder.splice(insertAt, 0, track.id);
      if (sameOrder(nextOrder, currentOrder)) return;
      currentOrder = nextOrder;
      setLocalTrackOrder(nextOrder);
    };

    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setLayerDrag(null);
      if (sameOrder(currentOrder, originalOrder)) {
        setLocalTrackOrder(null);
        return;
      }
      // Convert the Photoshop-style top-to-bottom UI order back to the
      // renderer's persisted bottom-to-top order.
      void Promise.resolve(onReorderTracks([...currentOrder].reverse())).catch(() => setLocalTrackOrder(null));
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function startLayerRename(track: TimelineTrack, index: number) {
    renameCancelledRef.current = false;
    setRenameLayer({ id: track.id, value: layerTitle(track, index), busy: false });
  }

  async function commitLayerRename(track: TimelineTrack) {
    if (!renameLayer || renameLayer.id !== track.id) return;
    if (renameCancelledRef.current) {
      renameCancelledRef.current = false;
      return;
    }
    const name = renameLayer.value.trim();
    if (!name) {
      setRenameLayer(null);
      return;
    }
    if (name === (track.name?.trim() || layerTitle(track, displayTracks.findIndex((item) => item.id === track.id)))) {
      setRenameLayer(null);
      return;
    }
    setRenameLayer((current) => {
      if (!current || current.id !== track.id) return current;
      return { ...current, busy: true };
    });
    try {
      await onRenameTrack(track, name);
      setRenameLayer(null);
    } catch {
      setRenameLayer((current) => {
        if (!current || current.id !== track.id) return current;
        return { ...current, busy: false };
      });
    }
  }

  function beginTrim(event: ReactPointerEvent<HTMLButtonElement>, track: TimelineTrack, clip: TimelineClip, edge: "left" | "right") {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(track.id, clip.id);
    const startX = event.clientX;
    const originalSourceStart = clip.source_start_us;
    const originalSourceEnd = clip.source_end_us;
    const originalTimelineStart = clip.timeline_start_us;
    const originalTimelineEnd = originalTimelineStart + clipDurationUs(clip);
    const speed = Math.max(0.05, Number(clip.speed ?? 1));
    const previousEnd = previousOccupiedEnd(track, clip);
    const nextStart = nextOccupiedStart(track, clip);
    let latest: TrimState = {
      key: `${track.id}:${clip.id}`,
      edge,
      sourceStartUs: originalSourceStart,
      sourceEndUs: originalSourceEnd,
      timelineStartUs: originalTimelineStart,
    };

    const update = (pointer: PointerEvent) => {
      const deltaTimelineUs = (pointer.clientX - startX) / pixelsPerSecond * 1_000_000;
      if (edge === "left") {
        const proposedTimelineStart = snapTimelineUs(originalTimelineStart + deltaTimelineUs);
        const maxTimelineStart = originalTimelineEnd - 10_000 / speed;
        const timelineStartUs = Math.min(maxTimelineStart, Math.max(previousEnd, proposedTimelineStart));
        const sourceStartUs = Math.min(originalSourceEnd - 10_000, Math.max(0, originalSourceStart + (timelineStartUs - originalTimelineStart) * speed));
        latest = {
          key: `${track.id}:${clip.id}`,
          edge,
          sourceStartUs: Math.round(sourceStartUs),
          sourceEndUs: originalSourceEnd,
          timelineStartUs: Math.round(originalTimelineStart + (sourceStartUs - originalSourceStart) / speed),
        };
      } else {
        const proposedTimelineEnd = snapTimelineUs(originalTimelineEnd + deltaTimelineUs);
        const maximumEnd = nextStart ?? Number.POSITIVE_INFINITY;
        const timelineEndUs = Math.max(originalTimelineStart + 10_000 / speed, Math.min(maximumEnd, proposedTimelineEnd));
        latest = {
          key: `${track.id}:${clip.id}`,
          edge,
          sourceStartUs: originalSourceStart,
          sourceEndUs: Math.max(originalSourceStart + 10_000, Math.round(originalSourceEnd + (timelineEndUs - originalTimelineEnd) * speed)),
          timelineStartUs: originalTimelineStart,
        };
      }
      setTrim(latest);
    };
    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setTrim(null);
      if (
        latest.sourceStartUs !== originalSourceStart
        || latest.sourceEndUs !== originalSourceEnd
        || latest.timelineStartUs !== originalTimelineStart
      ) {
        onTrimClip(track.id, clip, latest.sourceStartUs, latest.sourceEndUs, latest.timelineStartUs);
      }
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function beginEffectResize(
    event: ReactPointerEvent<HTMLSpanElement>,
    track: TimelineTrack,
    clip: TimelineClip,
    effect: TimelineEffect,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(track.id, clip.id);
    const startX = event.clientX;
    const originalDurationUs = effect.duration_us;
    const maximumDurationUs = Math.max(10_000, Math.round(clipDurationUs(clip)));
    const key = `${track.id}:${clip.id}:${effect.id}`;
    let durationUs = originalDurationUs;

    const update = (pointer: PointerEvent) => {
      const direction = effect.anchor === "END" ? -1 : 1;
      const deltaUs = direction * (pointer.clientX - startX) / pixelsPerSecond * 1_000_000;
      durationUs = Math.max(10_000, Math.min(maximumDurationUs, snapTimelineUs(originalDurationUs + deltaUs)));
      setEffectResize({ key, durationUs });
    };
    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setEffectResize(null);
      if (durationUs !== originalDurationUs) onUpdateEffect(track.id, clip, effect, durationUs);
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function closeAnd(action: () => void) {
    setContextMenu(null);
    action();
  }

  function laneEntries(track: TimelineTrack): Array<{ clip: TimelineClip; ownerTrack: TimelineTrack; isDragGhost: boolean }> {
    const entries: Array<{ clip: TimelineClip; ownerTrack: TimelineTrack; isDragGhost: boolean }> = [];
    for (const clip of Object.values(track.clips ?? {})) {
      const key = `${track.id}:${clip.id}`;
      if (drag?.key === key && drag.targetTrackId !== track.id) continue;
      entries.push({ clip, ownerTrack: track, isDragGhost: false });
    }
    if (drag && drag.targetTrackId === track.id && drag.sourceTrackId !== track.id) {
      const sourceTrack = displayTracks.find((item) => item.id === drag.sourceTrackId);
      const draggedClip = sourceTrack?.clips?.[drag.clipId];
      if (sourceTrack && draggedClip) {
        entries.push({
          clip: { ...draggedClip, timeline_start_us: drag.resolvedStartUs },
          ownerTrack: track,
          isDragGhost: true,
        });
      }
    }
    return entries;
  }

  const selectedTrack = displayTracks.find((track) => track.id === selectedTrackId);
  const selectedClip = selectedTrack?.clips?.[selectedClipId ?? ""];
  const selectedCanSplit = Boolean(
    selectedClip
    && playheadUs > selectedClip.timeline_start_us
    && playheadUs < selectedClip.timeline_start_us + clipDurationUs(selectedClip),
  );

  return (
    <section className="editor-timeline-shell">
      <div className="editor-transport">
        <div className="editor-transport__left">
          <Button variant="icon" size="sm" aria-label="Zum Anfang" onClick={() => onSeek(0)}><SkipBack size={16} /></Button>
          <Button variant="icon" size="sm" aria-label="Ein Bild zurück" onClick={() => onSeek(Math.max(0, playheadUs - 1_000_000 / fps))}><ChevronLeft size={16} /></Button>
          <Button variant="primary" size="sm" className="editor-play-button" aria-label={playing ? "Pause" : "Abspielen"} onClick={onPlayToggle}>{playing ? <Pause size={17} /> : <Play size={17} />}</Button>
          <Button variant="icon" size="sm" aria-label="Ein Bild vor" onClick={() => onSeek(playheadUs + 1_000_000 / fps)}><ChevronRight size={16} /></Button>
          <output className="editor-timecode">{formatTimecode(playheadUs, fps)}</output>
          <Button variant="icon" size="sm" aria-label="Ausgewähltes Element teilen" disabled={!selectedCanSplit || !selectedTrack || !selectedClip} onClick={() => { if (selectedTrack && selectedClip) onSplit(selectedTrack.id, selectedClip); }}><Scissors size={15} /></Button>
        </div>
        <div className="editor-transport__right">
          <ZoomOut size={14} />
          <input aria-label="Timeline-Zoom" type="range" min={minPps} max={maxPps} step={zoomStep} value={pixelsPerSecond} onChange={(event) => setPixelsPerSecond(Number(event.target.value))} />
          <ZoomIn size={14} />
          <Button variant="secondary" size="sm" onClick={() => onAddText(selectedTrackId ?? undefined, playheadUs)}><Type size={15} /> Text</Button>
          <Button variant="primary" size="sm" onClick={onAddMedia}><Plus size={15} /> Medien hinzufügen</Button>
        </div>
      </div>

      <div className="editor-timeline-body">
        <div ref={headersRef} className="editor-track-headers">
          <div className="editor-ruler-corner">Layer</div>
          {displayTracks.map((track, index) => (
            <div
              key={track.id}
              data-layer-header-id={track.id}
              className={`editor-track-header${layerDrag?.sourceId === track.id ? " is-reordering" : ""}`}
              onPointerDown={(event) => {
                if (event.target !== event.currentTarget) return;
                onTrackSelect(track.id);
              }}
            >
              <button
                type="button"
                className="editor-track-header__drag"
                aria-label={`${layerTitle(track, index)} verschieben`}
                title="Layer verschieben"
                onPointerDown={(event) => beginLayerReorder(event, track)}
              ><GripVertical size={15} /></button>
              <span className="editor-track-header__icon"><Layers3 size={14} /></span>
              <span className="editor-track-header__name">
                {renameLayer !== null && renameLayer.id === track.id ? (
                  <input
                    autoFocus
                    value={renameLayer.value}
                    disabled={renameLayer.busy}
                    aria-label="Layername"
                    maxLength={120}
                    onPointerDown={(event) => event.stopPropagation()}
                    onChange={(event) => setRenameLayer((current) => {
                      if (!current || current.id !== track.id) return current;
                      return { ...current, value: event.target.value };
                    })}
                    onBlur={() => { void commitLayerRename(track); }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur();
                      if (event.key === "Escape") {
                        renameCancelledRef.current = true;
                        setRenameLayer(null);
                      }
                    }}
                  />
                ) : (
                  <button type="button" onDoubleClick={() => startLayerRename(track, index)} onClick={() => onTrackSelect(track.id)}>
                    <strong>{layerTitle(track, index)}</strong>
                  </button>
                )}
                <small>{Object.keys(track.clips ?? {}).length} Elemente</small>
              </span>
              <Button variant="icon" size="sm" aria-label="Layer umbenennen" onClick={() => startLayerRename(track, index)}><Pencil size={12} /></Button>
              <Button variant="icon" size="sm" aria-label="Layer löschen" onClick={() => onDeleteTrack(track)}><Trash2 size={13} /></Button>
            </div>
          ))}
          {displayTracks.length === 0 ? <div className="editor-track-header editor-track-header--empty">Noch keine Layer</div> : null}
          <div className="editor-track-add"><button type="button" onClick={onAddTrack}><Plus size={13} /> Layer</button></div>
        </div>

        <div
          ref={viewportRef}
          className="editor-timeline-viewport"
          onScroll={(event) => {
            viewportScrollRef.current = { left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop };
            if (headersRef.current) headersRef.current.scrollTop = event.currentTarget.scrollTop;
          }}
        >
          <div className="editor-timeline-content" style={{ width: contentWidth }}>
            <div className="editor-ruler" onPointerDown={beginPlayheadDrag}>
              {ticks.map((seconds) => <span key={seconds} style={{ left: seconds * pixelsPerSecond }}><i />{rulerLabel(seconds)}</span>)}
            </div>
            <div className="editor-playhead" style={{ left: playheadUs / 1_000_000 * pixelsPerSecond }}><span /></div>

            {displayTracks.map((track) => (
              <div
                key={track.id}
                className={`editor-track-lane${drag?.targetTrackId === track.id ? " is-drop-target" : ""}`}
                data-track-id={track.id}
                onPointerDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  onTrackSelect(track.id);
                  onSeek(timeFromElementPoint(event.currentTarget, event.clientX));
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  onTrackSelect(track.id);
                  setContextMenu({
                    kind: "lane",
                    ...contextPosition(event.clientX, event.clientY),
                    track,
                    timeUs: timeFromElementPoint(event.currentTarget, event.clientX),
                  });
                }}
              >
                {laneEntries(track).map(({ clip, ownerTrack, isDragGhost }) => {
                  const key = `${ownerTrack.id}:${clip.id}`;
                  const isThisDragged = isDragGhost || Boolean(drag && drag.sourceTrackId === ownerTrack.id && drag.clipId === clip.id);
                  const trimValue = trim?.key === key ? trim : null;
                  const sourceStart = trimValue?.sourceStartUs ?? clip.source_start_us;
                  const sourceEnd = trimValue?.sourceEndUs ?? clip.source_end_us;
                  const timelineStart = isThisDragged ? drag!.resolvedStartUs : (trimValue?.timelineStartUs ?? clip.timeline_start_us);
                  const logicalDurationUs = (sourceEnd - sourceStart) / Math.max(0.05, Number(clip.speed ?? 1));
                  const left = Math.max(0, timelineStart / 1_000_000 * pixelsPerSecond);
                  const width = Math.max(22, logicalDurationUs / 1_000_000 * pixelsPerSecond);
                  const selected = ownerTrack.id === selectedTrackId && clip.id === selectedClipId;
                  const kind = elementKind(clip, ownerTrack, mediaFileTypes);
                  return (
                    <div
                      key={clip.id}
                      className={`editor-timeline-clip editor-timeline-clip--${kind.toLowerCase()}${selected ? " is-selected" : ""}${isThisDragged ? " is-dragging" : ""}`}
                      style={{ left, width }}
                      onPointerDown={(event) => beginMove(event, ownerTrack, clip)}
                      onDoubleClick={() => { if (kind === "TEXT") onEditText(ownerTrack.id, clip); }}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onSelect(ownerTrack.id, clip.id);
                        setContextMenu({ kind: "clip", ...contextPosition(event.clientX, event.clientY), track: ownerTrack, clip });
                      }}
                    >
                      <button className="editor-timeline-clip__trim editor-timeline-clip__trim--left" type="button" aria-label="Element-Anfang trimmen" onPointerDown={(event) => beginTrim(event, ownerTrack, clip, "left")} />
                      <span className="editor-timeline-clip__content">
                        <KindIcon kind={kind} />
                        <strong>{clipTitle(clip, kind, mediaTitles)}</strong>
                        {Boolean(clip.audio_muted) && (kind === "AUDIO" || kind === "VIDEO") ? <VolumeX size={12} /> : null}
                      </span>
                      {(clip.effects ?? []).filter((effect) => effect.type === "FADE" && effect.enabled !== false).map((effect) => {
                        const resizeKey = `${ownerTrack.id}:${clip.id}:${effect.id}`;
                        const effectDurationUs = effectResize?.key === resizeKey ? effectResize.durationUs : effect.duration_us;
                        const effectWidth = Math.max(18, Math.min(width, effectDurationUs / 1_000_000 * pixelsPerSecond));
                        return (
                          <span
                            key={effect.id}
                            className={`editor-timeline-effect editor-timeline-effect--${effect.anchor.toLowerCase()}`}
                            style={{ width: effectWidth }}
                            title={`${effect.anchor === "START" ? "Fade-In" : "Fade-Out"} · ${(effectDurationUs / 1_000_000).toFixed(1)} s · ziehen zum Skalieren`}
                            onPointerDown={(event) => beginEffectResize(event, ownerTrack, clip, effect)}
                          >
                            <Sparkles size={10} />
                            <button
                              type="button"
                              aria-label="Effekt entfernen"
                              onPointerDown={(event) => event.stopPropagation()}
                              onClick={() => onRemoveEffect(ownerTrack.id, clip, effect)}
                            ><X size={9} /></button>
                          </span>
                        );
                      })}
                      <button className="editor-timeline-clip__trim editor-timeline-clip__trim--right" type="button" aria-label="Element-Ende trimmen" onPointerDown={(event) => beginTrim(event, ownerTrack, clip, "right")} />
                    </div>
                  );
                })}
              </div>
            ))}

            {displayTracks.length === 0 ? <button type="button" className="editor-timeline-empty" onClick={onAddTrack}><Plus size={18} /> Ersten Layer hinzufügen</button> : null}
          </div>
        </div>
      </div>

      {contextMenu ? (
        <div className="editor-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
          {contextMenu.kind === "clip" ? (() => {
            const kind = elementKind(contextMenu.clip, contextMenu.track, mediaFileTypes);
            const fadeIn = effectByAnchor(contextMenu.clip, "START");
            const fadeOut = effectByAnchor(contextMenu.clip, "END");
            return (
              <>
                <button type="button" disabled={!(playheadUs > contextMenu.clip.timeline_start_us && playheadUs < contextMenu.clip.timeline_start_us + clipDurationUs(contextMenu.clip))} onClick={() => closeAnd(() => onSplit(contextMenu.track.id, contextMenu.clip))}><Scissors size={14} /> Am Abspielkopf teilen</button>
                {kind === "TEXT" ? <button type="button" onClick={() => closeAnd(() => onEditText(contextMenu.track.id, contextMenu.clip))}><Type size={14} /> Text bearbeiten</button> : null}
                {kind === "VIDEO" ? <button type="button" onClick={() => closeAnd(() => onSeparateAudio(contextMenu.track.id, contextMenu.clip))}><Unlink size={14} /> Video und Audio trennen</button> : null}
                {kind === "VIDEO" || kind === "AUDIO" ? <button type="button" onClick={() => closeAnd(() => onToggleMute(contextMenu.track.id, contextMenu.clip))}>{Boolean(contextMenu.clip.audio_muted) ? <Volume2 size={14} /> : <VolumeX size={14} />}{Boolean(contextMenu.clip.audio_muted) ? " Ton einschalten" : " Stummschalten"}</button> : null}
                <button type="button" onClick={() => closeAnd(() => onDuplicate(contextMenu.track.id, contextMenu.clip))}><Plus size={14} /> Duplizieren</button>
                <span className="editor-context-menu__separator" />
                <button type="button" onClick={() => closeAnd(() => fadeIn ? onRemoveEffect(contextMenu.track.id, contextMenu.clip, fadeIn) : onAddEffect(contextMenu.track.id, contextMenu.clip, "START"))}><Sparkles size={14} /> Fade-In {fadeIn ? "entfernen" : "hinzufügen"}</button>
                <button type="button" onClick={() => closeAnd(() => fadeOut ? onRemoveEffect(contextMenu.track.id, contextMenu.clip, fadeOut) : onAddEffect(contextMenu.track.id, contextMenu.clip, "END"))}><Sparkles size={14} /> Fade-Out {fadeOut ? "entfernen" : "hinzufügen"}</button>
                <span className="editor-context-menu__separator" />
                <button type="button" className="is-danger" onClick={() => closeAnd(() => onDeleteClip(contextMenu.track.id, contextMenu.clip))}><Trash2 size={14} /> Element entfernen</button>
              </>
            );
          })() : (
            <>
              <button type="button" onClick={() => closeAnd(() => { onTrackSelect(contextMenu.track.id); onSeek(contextMenu.timeUs); onAddMedia(); })}><Plus size={14} /> Medien hier hinzufügen</button>
              <button type="button" onClick={() => closeAnd(() => onAddText(contextMenu.track.id, contextMenu.timeUs))}><Type size={14} /> Text hier hinzufügen</button>
              <button type="button" onClick={() => closeAnd(onAddTrack)}><Layers3 size={14} /> Layer hinzufügen</button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
