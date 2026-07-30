import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/**
 * Normalized rectangle in source-video coordinates (0..1).
 * Matches the backend `NormalizedRegion` schema in video_cut/schemas.py.
 */
export interface NormalizedRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Interaction modes for an existing selection.
 * - "move": drag the whole rectangle
 * - "resize-nw" / "resize-ne" / "resize-sw" / "resize-se": drag a corner handle
 */
export type SelectionHandle = "move" | "resize-nw" | "resize-ne" | "resize-sw" | "resize-se";

interface DragState {
  handle: SelectionHandle;
  startX: number;
  startY: number;
  origin: NormalizedRegion;
}

const MIN_SIZE = 0.02;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizeDrag(
  origin: NormalizedRegion,
  handle: SelectionHandle,
  dx: number,
  dy: number,
): NormalizedRegion {
  let { x, y, width, height } = origin;
  if (handle === "move") {
    x = clamp(origin.x + dx, 0, 1 - origin.width);
    y = clamp(origin.y + dy, 0, 1 - origin.height);
    return { x, y, width, height };
  }
  const west = handle.endsWith("w");
  const east = handle.endsWith("e");
  const north = handle.includes("n");
  const south = handle.includes("s");
  if (west) {
    const nextX = clamp(origin.x + dx, 0, origin.x + origin.width - MIN_SIZE);
    width = origin.width + (origin.x - nextX);
    x = nextX;
  }
  if (east) {
    width = Math.max(MIN_SIZE, clamp(origin.width + dx, MIN_SIZE, 1 - origin.x));
  }
  if (north) {
    const nextY = clamp(origin.y + dy, 0, origin.y + origin.height - MIN_SIZE);
    height = origin.height + (origin.y - nextY);
    y = nextY;
  }
  if (south) {
    height = Math.max(MIN_SIZE, clamp(origin.height + dy, MIN_SIZE, 1 - origin.y));
  }
  return { x, y, width, height };
}

/**
 * Headless pointer-driven selection over a rectangular surface.
 *
 * The hook is agnostic about how the surface is rendered — the caller passes
 * a ref to the bounding element (e.g. the video wrapper) and the hook
 * converts pointer deltas into normalized 0..1 coordinates against that
 * element's bounding box.
 *
 * Two interactions are supported:
 * 1. **Draw**: pointer-down on the surface background starts a new rectangle.
 * 2. **Adjust**: pointer-down on the rectangle or one of its handles moves or
 *    resizes the existing rectangle.
 *
 * The hook returns the current region (live during drag), the committed
 * region, a setter (for presets / clear), and pointer handlers to attach to
 * the surface and the handles.
 */
export function useRegionSelection(
  surfaceRef: React.RefObject<HTMLElement | null>,
  initial: NormalizedRegion | null = null,
) {
  const [region, setRegion] = useState<NormalizedRegion | null>(initial);
  const [draft, setDraft] = useState<NormalizedRegion | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const latestDraftRef = useRef<NormalizedRegion | null>(null);

  // Keep latestDraftRef in sync so the pointerup handler can commit without
  // calling setRegion from inside setDraft's updater.
  useEffect(() => {
    latestDraftRef.current = draft;
  }, [draft]);

  const handleMove = useCallback((event: PointerEvent) => {
    const drag = dragRef.current;
    const surface = surfaceRef.current;
    if (!drag || !surface) return;
    const rect = surface.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const dx = (event.clientX - drag.startX) / rect.width;
    const dy = (event.clientY - drag.startY) / rect.height;
    setDraft(normalizeDrag(drag.origin, drag.handle, dx, dy));
  }, [surfaceRef]);

  const handleUp = useCallback(() => {
    dragRef.current = null;
    window.removeEventListener("pointermove", handleMove);
    window.removeEventListener("pointerup", handleUp);
    const final = latestDraftRef.current;
    setDraft(null);
    if (final && final.width >= MIN_SIZE && final.height >= MIN_SIZE) {
      setRegion(final);
    }
  }, [handleMove]);

  /** Start drawing a new rectangle from a pointer-down on the surface. */
  const beginDraw = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const surface = surfaceRef.current;
    if (!surface) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = surface.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const originX = clamp((startX - rect.left) / rect.width, 0, 1);
    const originY = clamp((startY - rect.top) / rect.height, 0, 1);
    // Start with a zero-size rectangle at the click point; the drag expands it.
    const origin: NormalizedRegion = { x: originX, y: originY, width: 0, height: 0 };
    dragRef.current = { handle: "resize-se", startX, startY, origin };
    setDraft(origin);
    latestDraftRef.current = origin;
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }, [handleMove, handleUp, surfaceRef]);

  /** Begin moving or resizing an existing rectangle. */
  const beginAdjust = useCallback(
    (event: ReactPointerEvent<HTMLElement>, handle: SelectionHandle) => {
      if (!region) return;
      event.preventDefault();
      event.stopPropagation();
      dragRef.current = {
        handle,
        startX: event.clientX,
        startY: event.clientY,
        origin: region,
      };
      setDraft(region);
      latestDraftRef.current = region;
      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
    },
    [handleMove, handleUp, region],
  );

  const clear = useCallback(() => {
    dragRef.current = null;
    window.removeEventListener("pointermove", handleMove);
    window.removeEventListener("pointerup", handleUp);
    setDraft(null);
    latestDraftRef.current = null;
    setRegion(null);
  }, [handleMove, handleUp]);

  const active = draft ?? region;

  return {
    region: active,
    committed: region,
    beginDraw,
    beginAdjust,
    setRegion,
    clear,
    isDragging: dragRef.current !== null,
  };
}
