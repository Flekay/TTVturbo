import {
  Crop,
  Film,
  Hand,
  Move,
  RotateCw,
  ZoomIn,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from "react";
import type { EditSequence, TimelineClip, TimelineTrack } from "../../features/projects/api";
import { libraryItemFileUrl } from "../../features/library/api";
import type { NormalizedRegion } from "../../features/videoCut";
import { CanvasTooltip } from "./CanvasTooltip";

export interface CanvasTransform {
  x: number;
  y: number;
  scale_x: number;
  scale_y: number;
  rotation: number;
}

type ToolMode = "move" | "select" | "zoom" | "pan";

interface ViewportState {
  zoom: number;
  panX: number;
  panY: number;
}

interface CropState {
  clipKey: string;
  trackId: string;
  clipId: string;
  region: NormalizedRegion;
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
  onAddMedia?: () => void;
  onCutRegion?: (trackId: string, clip: TimelineClip, region: NormalizedRegion) => void;
  onTogglePlay?: () => void;
}

interface ActiveClip {
  key: string;
  track: TimelineTrack;
  clip: TimelineClip;
  visual: boolean;
}

type InteractionMode = "move" | "resize-nw" | "resize-ne" | "resize-sw" | "resize-se" | "rotate";
type CropHandle = "move" | "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se";

const VISUAL_TRACKS = new Set(["VIDEO", "GAMEPLAY", "FACECAM", "OVERLAY"]);
const AUDIO_TRACKS = new Set(["VIDEO", "GAMEPLAY", "FACECAM", "AUDIO"]);

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;
const DEFAULT_ZOOM = 0.25;
const MIN_CROP_SIZE = 0.02;
const SPACE_HOLD_THRESHOLD = 150; // ms — short press = play, long hold = pan

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

function transformsEqual(a: CanvasTransform, b: CanvasTransform): boolean {
  return a.x === b.x && a.y === b.y && a.scale_x === b.scale_x && a.scale_y === b.scale_y && a.rotation === b.rotation;
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
  onCutRegion,
  onTogglePlay,
}: EditorCanvasProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const mediaRefs = useRef(new Map<string, HTMLMediaElement>());
  const [drafts, setDrafts] = useState<Record<string, CanvasTransform>>({});
  const [toolMode, setToolMode] = useState<ToolMode>("move");
  const [viewport, setViewport] = useState<ViewportState>({ zoom: DEFAULT_ZOOM, panX: 0, panY: 0 });
  const [crop, setCrop] = useState<CropState | null>(null);
  const [cropDraft, setCropDraft] = useState<NormalizedRegion | null>(null);
  const [spacePanning, setSpacePanning] = useState(false);
  const [videoDims, setVideoDims] = useState<Record<string, { w: number; h: number }>>({});

  // Refs for space-key logic (short press = play, long hold = pan)
  const spaceDownRef = useRef<number>(0);
  const spacePanArmedRef = useRef(false);

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

  // --- Fit the sequence to the viewport on mount and on resize ---
  useEffect(() => {
    const viewportEl = viewportRef.current;
    if (!viewportEl) return;
    const rect = viewportEl.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const padding = 40;
    const zoomX = (rect.width - padding * 2) / sequence.width;
    const zoomY = (rect.height - padding * 2) / sequence.height;
    const zoom = clamp(Math.min(zoomX, zoomY), MIN_ZOOM, MAX_ZOOM);
    setViewport({
      zoom,
      panX: (rect.width - sequence.width * zoom) / 2,
      panY: (rect.height - sequence.height * zoom) / 2,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-fit on window resize.
  useEffect(() => {
    const handleResize = () => {
      const viewportEl = viewportRef.current;
      if (!viewportEl) return;
      const rect = viewportEl.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const padding = 40;
      const zoomX = (rect.width - padding * 2) / sequence.width;
      const zoomY = (rect.height - padding * 2) / sequence.height;
      const zoom = clamp(Math.min(zoomX, zoomY), MIN_ZOOM, MAX_ZOOM);
      setViewport({
        zoom,
        panX: (rect.width - sequence.width * zoom) / 2,
        panY: (rect.height - sequence.height * zoom) / 2,
      });
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [sequence.width, sequence.height]);

  // --- On switching to crop tool, start cropping the currently selected clip ---
  useEffect(() => {
    if (toolMode !== "select") {
      setCrop(null);
      return;
    }
    if (selectedTrackId && selectedClipId) {
      const entry = active.find((e) => e.track.id === selectedTrackId && e.clip.id === selectedClipId);
      if (entry) startCropForClip(entry);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolMode]);

  // --- Space key: short press = play/pause, long hold = pan ---
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.code !== "Space") return;
      // Prevent the workspace-level space handler from also firing.
      event.preventDefault();
      event.stopPropagation();
      if (spaceDownRef.current > 0) return; // ignore auto-repeat
      spaceDownRef.current = Date.now();
      spacePanArmedRef.current = false;
      // Arm the pan mode after the threshold.
      window.setTimeout(() => {
        if (spaceDownRef.current > 0) {
          spacePanArmedRef.current = true;
          setSpacePanning(true);
        }
      }, SPACE_HOLD_THRESHOLD);
    };
    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      const wasArmed = spacePanArmedRef.current;
      spaceDownRef.current = 0;
      spacePanArmedRef.current = false;
      setSpacePanning(false);
      if (!wasArmed) {
        // Short press → toggle play/pause
        onTogglePlay?.();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [onTogglePlay]);

  // --- ESC to cancel crop ---
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && crop) {
        setCrop(null);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [crop]);

  // --- Tool hotkeys: V=Move, C=Crop, Z=Zoom, H=Pan ---
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      switch (event.key.toLowerCase()) {
        case "v": setToolMode("move"); break;
        case "c": setToolMode("select"); break;
        case "z": setToolMode("zoom"); break;
        case "h": setToolMode("pan"); break;
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  // The effective tool is overridden by space-panning.
  const effectiveTool: ToolMode = spacePanning ? "pan" : toolMode;

  // --- Viewport helpers ---

  const fitToViewport = useCallback(() => {
    const viewportEl = viewportRef.current;
    if (!viewportEl) return;
    const rect = viewportEl.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const padding = 40;
    const zoomX = (rect.width - padding * 2) / sequence.width;
    const zoomY = (rect.height - padding * 2) / sequence.height;
    const zoom = clamp(Math.min(zoomX, zoomY), MIN_ZOOM, MAX_ZOOM);
    const panX = (rect.width - sequence.width * zoom) / 2;
    const panY = (rect.height - sequence.height * zoom) / 2;
    setViewport({ zoom, panX, panY });
  }, [sequence.width, sequence.height]);

  const zoomAt = useCallback((clientX: number, clientY: number, factor: number) => {
    const viewportEl = viewportRef.current;
    if (!viewportEl) return;
    const rect = viewportEl.getBoundingClientRect();
    const newZoom = clamp(viewport.zoom * factor, MIN_ZOOM, MAX_ZOOM);
    if (newZoom === viewport.zoom) return;
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;
    const worldX = (localX - viewport.panX) / viewport.zoom;
    const worldY = (localY - viewport.panY) / viewport.zoom;
    setViewport({
      zoom: newZoom,
      panX: localX - worldX * newZoom,
      panY: localY - worldY * newZoom,
    });
  }, [viewport]);

  const handleWheel = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomAt(event.clientX, event.clientY, factor);
  }, [zoomAt]);

  // --- Zoom tool: click to zoom in, Alt+click to zoom out ---
  const handleZoomClick = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    zoomAt(event.clientX, event.clientY, event.altKey ? 1 / 1.25 : 1.25);
  }, [zoomAt]);

  // --- Pan handling (hand tool, space-hold, or middle mouse) ---

  const beginPan = useCallback((event: { clientX: number; clientY: number; preventDefault?: () => void; stopPropagation?: () => void }) => {
    event.preventDefault?.();
    event.stopPropagation?.();
    const startX = event.clientX;
    const startY = event.clientY;
    const startPanX = viewport.panX;
    const startPanY = viewport.panY;
    const move = (pointer: PointerEvent) => {
      setViewport((current) => ({ ...current, panX: startPanX + (pointer.clientX - startX), panY: startPanY + (pointer.clientY - startY) }));
    };
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  }, [viewport.panX, viewport.panY]);

  // --- Clip interaction (move/resize/rotate) ---

  function beginInteraction(
    event: ReactPointerEvent<HTMLElement>,
    entry: ActiveClip,
    mode: InteractionMode,
  ) {
    event.preventDefault();
    event.stopPropagation();
    onSelect(entry.track.id, entry.clip.id);
    const viewportEl = viewportRef.current;
    if (!viewportEl) return;
    const rect = viewportEl.getBoundingClientRect();
    const start = drafts[entry.key] ?? transformOf(entry.clip);
    const startX = event.clientX;
    const startY = event.clientY;
    const clipCenterScreenX = rect.left + viewport.panX + (start.x + start.scale_x / 2) * sequence.width * viewport.zoom;
    const clipCenterScreenY = rect.top + viewport.panY + (start.y + start.scale_y / 2) * sequence.height * viewport.zoom;
    const startAngle = Math.atan2(event.clientY - clipCenterScreenY, event.clientX - clipCenterScreenX) * 180 / Math.PI;
    let latest = start;
    const proportional = !event.shiftKey;

    const update = (pointer: PointerEvent) => {
      const dx = (pointer.clientX - startX) / (sequence.width * viewport.zoom);
      const dy = (pointer.clientY - startY) / (sequence.height * viewport.zoom);
      let next = { ...start };
      if (mode === "move") {
        next.x = start.x + dx;
        next.y = start.y + dy;
      } else if (mode === "rotate") {
        const angle = Math.atan2(pointer.clientY - clipCenterScreenY, pointer.clientX - clipCenterScreenX) * 180 / Math.PI;
        next.rotation = Math.round((start.rotation + angle - startAngle) * 10) / 10;
      } else {
        const west = mode.endsWith("w");
        const east = mode.endsWith("e");
        const north = mode.includes("n");
        const south = mode.includes("s");
        let newScaleX = start.scale_x;
        let newScaleY = start.scale_y;
        let newX = start.x;
        let newY = start.y;
        if (west) {
          newScaleX = Math.max(0.03, start.scale_x - dx);
          newX = start.x + start.scale_x - newScaleX;
        }
        if (east) newScaleX = Math.max(0.03, start.scale_x + dx);
        if (north) {
          newScaleY = Math.max(0.03, start.scale_y - dy);
          newY = start.y + start.scale_y - newScaleY;
        }
        if (south) newScaleY = Math.max(0.03, start.scale_y + dy);
        // Proportional: lock aspect ratio unless Shift is held.
        if (proportional && (east || west) && (north || south)) {
          const ratio = start.scale_x / Math.max(0.001, start.scale_y);
          if (newScaleX / Math.max(0.001, newScaleY) > ratio) {
            const targetY = newScaleX / ratio;
            if (north) newY = start.y + start.scale_y - targetY;
            newScaleY = targetY;
          } else {
            const targetX = newScaleY * ratio;
            if (west) newX = start.x + start.scale_x - targetX;
            newScaleX = targetX;
          }
        }
        next.x = newX;
        next.y = newY;
        next.scale_x = newScaleX;
        next.scale_y = newScaleY;
      }
      latest = next;
      setDrafts((current) => ({ ...current, [entry.key]: next }));
    };

    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      // Only commit if the transform actually changed — clicking without
      // dragging should NOT create a history entry.
      if (!transformsEqual(latest, start)) {
        Promise.resolve(onTransformCommit(entry.track.id, entry.clip.id, latest)).finally(() => {
          setDrafts((current) => {
            const next = { ...current };
            delete next[entry.key];
            return next;
          });
        });
      } else {
        setDrafts((current) => {
          const next = { ...current };
          delete next[entry.key];
          return next;
        });
      }
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  // --- Crop tool (select tool = crop) ---

  function startCropForClip(entry: ActiveClip) {
    onSelect(entry.track.id, entry.clip.id);
    setCrop({
      clipKey: entry.key,
      trackId: entry.track.id,
      clipId: entry.clip.id,
      region: { x: 0, y: 0, width: 1, height: 1 },
    });
  }

  function beginCropAdjust(event: ReactPointerEvent<HTMLElement>, handle: CropHandle) {
    if (!crop) return;
    event.preventDefault();
    event.stopPropagation();
    const clipEl = event.currentTarget.closest("[data-clip-key]") as HTMLElement | null;
    if (!clipEl) return;
    const contentEl = clipEl.querySelector("[data-content-frame]") as HTMLElement | null;
    const rect = (contentEl ?? clipEl).getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = crop.region;
    let latest = origin;

    const update = (pointer: PointerEvent) => {
      const dx = (pointer.clientX - startX) / rect.width;
      const dy = (pointer.clientY - startY) / rect.height;
      if (handle === "move") {
        // Move: clamp position so the crop stays fully inside 0..1,
        // but keep width/height unchanged.
        latest = {
          x: clamp(origin.x + dx, 0, 1 - origin.width),
          y: clamp(origin.y + dy, 0, 1 - origin.height),
          width: origin.width,
          height: origin.height,
        };
      } else {
        let { x, y, width, height } = origin;
        if (handle.includes("w")) {
          const newX = clamp(origin.x + dx, 0, origin.x + origin.width - MIN_CROP_SIZE);
          width = origin.width + (origin.x - newX);
          x = newX;
        }
        if (handle.includes("e")) {
          width = Math.max(MIN_CROP_SIZE, clamp(origin.width + dx, MIN_CROP_SIZE, 1 - origin.x));
        }
        if (handle.includes("n")) {
          const newY = clamp(origin.y + dy, 0, origin.y + origin.height - MIN_CROP_SIZE);
          height = origin.height + (origin.y - newY);
          y = newY;
        }
        if (handle.includes("s")) {
          height = Math.max(MIN_CROP_SIZE, clamp(origin.height + dy, MIN_CROP_SIZE, 1 - origin.y));
        }
        latest = { x, y, width, height };
      }
      setCropDraft(latest);
    };

    const finish = () => {
      window.removeEventListener("pointermove", update);
      window.removeEventListener("pointerup", finish);
      setCropDraft(null);
      setCrop((current) => current ? { ...current, region: latest } : null);
    };
    window.addEventListener("pointermove", update);
    window.addEventListener("pointerup", finish, { once: true });
  }

  function handleClipPointerDown(event: ReactPointerEvent<HTMLElement>, entry: ActiveClip) {
    // Middle mouse → pan regardless of tool
    if (event.button === 1) {
      beginPan(event);
      return;
    }
    if (effectiveTool === "pan") return; // let viewport handle it
    event.stopPropagation();
    if (effectiveTool === "zoom") {
      handleZoomClick(event);
      return;
    }
    if (effectiveTool === "select") {
      const target = event.target as HTMLElement;
      if (target.dataset.cropHandle) {
        beginCropAdjust(event, target.dataset.cropHandle as CropHandle);
      } else if (crop?.clipKey === entry.key) {
        beginCropAdjust(event, "move");
      } else {
        startCropForClip(entry);
      }
      return;
    }
    // Default: move mode
    beginInteraction(event, entry, "move");
  }

  function handleViewportPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    // Middle mouse → pan regardless of tool
    if (event.button === 1) {
      beginPan(event);
      return;
    }
    if (effectiveTool === "pan") {
      beginPan(event);
      return;
    }
    if (effectiveTool === "zoom") {
      handleZoomClick(event);
      return;
    }
    // move/select: only handle background clicks (not on clips)
    if ((event.target as HTMLElement).closest("[data-clip-key]")) return;
    if (effectiveTool === "select") setCrop(null);
    onSelect("", "");
  }

  const activeCropRegion = cropDraft ?? crop?.region ?? null;
  const worldStyle = {
    transform: `translate(${viewport.panX}px, ${viewport.panY}px) scale(${viewport.zoom})`,
    transformOrigin: "0 0",
  };

  // Zoom-compensated UI sizes: divide by zoom so they appear constant on screen.
  const z = viewport.zoom;
  const borderWidth = 2 / z;
  const handleSize = 10 / z;
  const handleOffset = 6 / z;
  const rotateSize = 24 / z;
  const rotateOffset = 30 / z;
  const labelFontSize = 10 / z;
  const labelPadding = 3 / z;
  const labelRadius = 4 / z;
  const labelGap = 5 / z;
  const labelMaxWidth = 190 / z;
  const handleRadius = 2 / z;
  const handleBorder = 2 / z;
  const cropBorderWidth = 2 / z;
  const cropHandleSize = 12 / z;
  const cropHandleOffset = 7 / z;
  const cropEdgeThickness = 8 / z;
  const cropEdgeOffset = 4 / z;
  const cropEdgeInset = 12 / z;

  const handleStyle = (extra: Record<string, string | number>): Record<string, string | number> => ({
    width: `${handleSize}px`,
    height: `${handleSize}px`,
    borderRadius: `${handleRadius}px`,
    borderWidth: `${handleBorder}px`,
    ...extra,
  });

  return (
    <section className="editor-stage-shell">
      <div
        ref={viewportRef}
        className={`editor-stage-viewport editor-stage-viewport--tool-${effectiveTool}`}
        onWheel={handleWheel}
        onPointerDown={handleViewportPointerDown}
        onAuxClick={(event) => event.preventDefault()} // prevent middle-click auto-scroll
      >
        <div className="editor-stage-world" style={worldStyle}>
          <div
            className="editor-stage"
            style={{ width: `${sequence.width}px`, height: `${sequence.height}px` }}
          >
            {sequence.safe_area_enabled !== false ? (() => {
              const pct = (v: number, max: number) => `${Math.max(0, Math.min(Number(v) || 0, max)) / Math.max(1, max) * 100}%`;
              return (
                <div
                  className="editor-stage__safe-area"
                  style={{
                    top: pct(sequence.safe_area_margin_top ?? 80, sequence.height),
                    right: pct(sequence.safe_area_margin_right ?? 80, sequence.width),
                    bottom: pct(sequence.safe_area_margin_bottom ?? 80, sequence.height),
                    left: pct(sequence.safe_area_margin_left ?? 80, sequence.width),
                  }}
                />
              );
            })() : null}
            {active.filter((entry) => entry.visual).map((entry, index) => {
              const selected = entry.track.id === selectedTrackId && entry.clip.id === selectedClipId;
              const value = drafts[entry.key] ?? transformOf(entry.clip);
              const hasCrop = crop?.clipKey === entry.key;
              const cropRegion = hasCrop ? activeCropRegion : null;
              const isStretched = Math.abs(value.scale_x - value.scale_y) > 0.01;
              const containerW = value.scale_x * sequence.width;
              const containerH = value.scale_y * sequence.height;
              const dims = videoDims[entry.key];
              let contentLeft = 0, contentTop = 0, contentWidth = containerW, contentHeight = containerH;
              if (dims && !isStretched) {
                const videoAspect = dims.w / dims.h;
                const containerAspect = containerW / containerH;
                if (videoAspect > containerAspect) {
                  contentHeight = containerW / videoAspect;
                  contentTop = (containerH - contentHeight) / 2;
                } else {
                  contentWidth = containerH * videoAspect;
                  contentLeft = (containerW - contentWidth) / 2;
                }
              }
              return (
                <div
                  key={entry.key}
                  data-clip-key={entry.key}
                  className={`editor-stage-item${selected && effectiveTool === "move" ? " is-selected" : ""}`}
                  style={{
                    left: `${value.x * sequence.width}px`,
                    top: `${value.y * sequence.height}px`,
                    width: `${containerW}px`,
                    height: `${containerH}px`,
                    transform: `rotate(${value.rotation}deg)`,
                    opacity: Number(entry.clip.opacity ?? 1),
                    zIndex: index + 1,
                  }}
                  onPointerDown={(event) => handleClipPointerDown(event, entry)}
                  onDoubleClick={() => onSelect(entry.track.id, entry.clip.id)}
                >
                  <video
                    ref={(node) => { if (node) mediaRefs.current.set(entry.key, node); else mediaRefs.current.delete(entry.key); }}
                    src={libraryItemFileUrl(entry.clip.source_media_item_id)}
                    muted={Boolean(entry.clip.audio_muted)}
                    playsInline
                    preload="auto"
                    draggable={false}
                    onLoadedMetadata={(e) => {
                      const v = e.currentTarget;
                      if (v.videoWidth && v.videoHeight) {
                        setVideoDims((prev) => prev[entry.key] ? prev : { ...prev, [entry.key]: { w: v.videoWidth, h: v.videoHeight } });
                      }
                    }}
                    style={{ objectFit: isStretched ? "fill" : "contain" }}
                  />
                  {/* Content frame: border + handles match the actual video display area */}
                  <div
                    data-content-frame
                    className="editor-stage-item__content"
                    style={{
                      left: `${contentLeft}px`,
                      top: `${contentTop}px`,
                      width: `${contentWidth}px`,
                      height: `${contentHeight}px`,
                      ...(selected && effectiveTool === "move" ? { border: `${borderWidth}px solid var(--color-accent)`, boxSizing: "border-box" } : {}),
                    }}
                  >
                    {selected && effectiveTool === "move" ? (
                      <>
                        <span className="editor-stage-item__label" style={{ fontSize: `${labelFontSize}px`, padding: `${labelPadding}px ${labelPadding * 2}px`, borderRadius: `${labelRadius}px`, gap: `${labelGap}px`, maxWidth: `${labelMaxWidth}px`, bottom: `calc(100% + ${labelPadding * 1.5}px)` }}><Move size={12 / z} /> {mediaTitles[entry.clip.source_media_item_id] ?? "Clip"}</span>
                        <button type="button" className="editor-stage-handle editor-stage-handle--nw" aria-label="Oben links skalieren" style={handleStyle({ left: `${-handleOffset}px`, top: `${-handleOffset}px` })} onPointerDown={(event) => { event.stopPropagation(); beginInteraction(event, entry, "resize-nw"); }} />
                        <button type="button" className="editor-stage-handle editor-stage-handle--ne" aria-label="Oben rechts skalieren" style={handleStyle({ right: `${-handleOffset}px`, top: `${-handleOffset}px` })} onPointerDown={(event) => { event.stopPropagation(); beginInteraction(event, entry, "resize-ne"); }} />
                        <button type="button" className="editor-stage-handle editor-stage-handle--sw" aria-label="Unten links skalieren" style={handleStyle({ left: `${-handleOffset}px`, bottom: `${-handleOffset}px` })} onPointerDown={(event) => { event.stopPropagation(); beginInteraction(event, entry, "resize-sw"); }} />
                        <button type="button" className="editor-stage-handle editor-stage-handle--se" aria-label="Unten rechts skalieren" style={handleStyle({ right: `${-handleOffset}px`, bottom: `${-handleOffset}px` })} onPointerDown={(event) => { event.stopPropagation(); beginInteraction(event, entry, "resize-se"); }} />
                        <button type="button" className="editor-stage-rotate" aria-label="Rotieren" style={{ width: `${rotateSize}px`, height: `${rotateSize}px`, bottom: `calc(100% + ${rotateOffset}px)` }} onPointerDown={(event) => { event.stopPropagation(); beginInteraction(event, entry, "rotate"); }}><RotateCw size={12 / z} /></button>
                      </>
                    ) : null}
                    {cropRegion ? (
                      <div
                        className="editor-stage-crop"
                        style={{
                          left: `${cropRegion.x * 100}%`,
                          top: `${cropRegion.y * 100}%`,
                          width: `${cropRegion.width * 100}%`,
                          height: `${cropRegion.height * 100}%`,
                          borderWidth: `${cropBorderWidth}px`,
                        }}
                        onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "move"); }}
                      >
                        {/* Edge handles */}
                        <span className="editor-stage-crop__edge editor-stage-crop__edge--n" data-crop-handle="n" style={{ top: `${-cropEdgeOffset}px`, left: `${cropEdgeInset}px`, right: `${cropEdgeInset}px`, height: `${cropEdgeThickness}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "n"); }} />
                        <span className="editor-stage-crop__edge editor-stage-crop__edge--s" data-crop-handle="s" style={{ bottom: `${-cropEdgeOffset}px`, left: `${cropEdgeInset}px`, right: `${cropEdgeInset}px`, height: `${cropEdgeThickness}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "s"); }} />
                        <span className="editor-stage-crop__edge editor-stage-crop__edge--w" data-crop-handle="w" style={{ left: `${-cropEdgeOffset}px`, top: `${cropEdgeInset}px`, bottom: `${cropEdgeInset}px`, width: `${cropEdgeThickness}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "w"); }} />
                        <span className="editor-stage-crop__edge editor-stage-crop__edge--e" data-crop-handle="e" style={{ right: `${-cropEdgeOffset}px`, top: `${cropEdgeInset}px`, bottom: `${cropEdgeInset}px`, width: `${cropEdgeThickness}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "e"); }} />
                        {/* Corner handles */}
                        <span className="editor-stage-crop__handle editor-stage-crop__handle--nw" data-crop-handle="nw" style={{ width: `${cropHandleSize}px`, height: `${cropHandleSize}px`, top: `${-cropHandleOffset}px`, left: `${-cropHandleOffset}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "nw"); }} />
                        <span className="editor-stage-crop__handle editor-stage-crop__handle--ne" data-crop-handle="ne" style={{ width: `${cropHandleSize}px`, height: `${cropHandleSize}px`, top: `${-cropHandleOffset}px`, right: `${-cropHandleOffset}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "ne"); }} />
                        <span className="editor-stage-crop__handle editor-stage-crop__handle--sw" data-crop-handle="sw" style={{ width: `${cropHandleSize}px`, height: `${cropHandleSize}px`, bottom: `${-cropHandleOffset}px`, left: `${-cropHandleOffset}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "sw"); }} />
                        <span className="editor-stage-crop__handle editor-stage-crop__handle--se" data-crop-handle="se" style={{ width: `${cropHandleSize}px`, height: `${cropHandleSize}px`, bottom: `${-cropHandleOffset}px`, right: `${-cropHandleOffset}px` }} onPointerDown={(event) => { event.stopPropagation(); beginCropAdjust(event, "se"); }} />
                      </div>
                    ) : null}
                  </div>
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
              <div className="editor-stage-empty" style={{ transform: "scale(2)", transformOrigin: "center" }}>
                <Film size={48} />
                <strong>Leere Szene</strong>
                <span>Medien hinzufügen oder an diese Position ziehen.</span>
              </div>
            ) : null}
          </div>
        </div>

        {/* Toolbar (vertical, left side) */}
        <div className="editor-canvas-toolbar" onPointerDown={(event) => event.stopPropagation()}>
          <CanvasTooltip
            title="Verschieben"
            shortcut="V"
            description="Clips verschieben, skalieren und rotieren. Standardwerkzeug für Positionierung. Shift beim Skalieren hält das Seitenverhältnis nicht."
          >
            <button type="button" className={toolMode === "move" ? "is-active" : ""} onClick={() => setToolMode("move")}><Move size={15} /></button>
          </CanvasTooltip>
          <CanvasTooltip
            title="Zuschneiden"
            shortcut="C"
            description="Video auf einen Bereich zuschneiden. Klicke einen Clip an, ziehe die roten Ränder rein und bestätige. Das Original wird durch den Zuschnitt ersetzt (rückgängig machbar)."
          >
            <button type="button" className={toolMode === "select" ? "is-active" : ""} onClick={() => setToolMode("select")}><Crop size={15} /></button>
          </CanvasTooltip>
          <span className="editor-canvas-toolbar__divider" />
          <CanvasTooltip
            title="Zoom"
            shortcut="Z"
            description="Klick reinzoomen, Alt+Klick rauszoomen. Mausrad zoomt jederzeit am Cursor."
          >
            <button type="button" className={toolMode === "zoom" ? "is-active" : ""} onClick={() => setToolMode("zoom")}><ZoomIn size={15} /></button>
          </CanvasTooltip>
          <CanvasTooltip
            title="Verschieben (Ansicht)"
            shortcut="H · Leertaste (halten)"
            description="Die Arbeitsfläche verschieben. Auch mit gedrückter Leertaste oder mittlerer Maustaste in jedem Werkzeug verfügbar."
          >
            <button type="button" className={toolMode === "pan" ? "is-active" : ""} onClick={() => setToolMode("pan")}><Hand size={15} /></button>
          </CanvasTooltip>
        </div>

        {/* Zoom indicator (click = fit to viewport) */}
        <button
          type="button"
          className="editor-canvas-zoom-indicator"
          onClick={fitToViewport}
          onPointerDown={(event) => event.stopPropagation()}
          title="Klicken um Ansicht anzupassen"
        >
          {Math.round(viewport.zoom * 100)}%
        </button>

        {/* Crop action bar */}
        {crop && effectiveTool === "select" ? (
          <div className="editor-canvas-crop-bar" onPointerDown={(event) => event.stopPropagation()}>
            <span>Zuschneiden: {(crop.region.width * 100).toFixed(0)}% × {(crop.region.height * 100).toFixed(0)}%</span>
            <button type="button" className="btn btn--primary btn--sm" onClick={() => {
              const entry = active.find((e) => e.key === crop.clipKey);
              if (entry) onCutRegion?.(crop.trackId, entry.clip, crop.region);
              setCrop(null);
            }}><Crop size={13} /> Zuschneiden bestätigen</button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setCrop(null)}>Abbrechen</button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
