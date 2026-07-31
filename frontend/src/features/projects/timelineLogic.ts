import type {
  TimelineClip,
  TimelineEffect,
  TimelineElementKind,
  TimelineTrack,
} from "./api";

export const TIMELINE_SNAP_US = 100_000;
export const MIN_ELEMENT_DURATION_US = 10_000;

export interface OccupiedRange {
  clipId: string;
  startUs: number;
  endUs: number;
}

export function clipDurationUs(clip: TimelineClip): number {
  return Math.max(
    1,
    (clip.source_end_us - clip.source_start_us) / Math.max(0.05, Number(clip.speed ?? 1)),
  );
}

export function clipEndUs(clip: TimelineClip): number {
  return clip.timeline_start_us + clipDurationUs(clip);
}

export function snapTimelineUs(value: number): number {
  return Math.max(0, Math.round(value / TIMELINE_SNAP_US) * TIMELINE_SNAP_US);
}

export function elementKind(
  clip: TimelineClip,
  track?: TimelineTrack,
  mediaFileTypes: Record<string, string> = {},
): TimelineElementKind {
  if (clip.kind === "VIDEO" || clip.kind === "AUDIO" || clip.kind === "IMAGE" || clip.kind === "TEXT") {
    return clip.kind;
  }
  const fileType = mediaFileTypes[clip.source_media_item_id];
  if (fileType === "audio") return "AUDIO";
  if (fileType === "image") return "IMAGE";
  if (track?.type === "AUDIO") return "AUDIO";
  if (track?.type === "CAPTIONS") return "TEXT";
  return "VIDEO";
}

export function occupiedRanges(track: TimelineTrack, excludingClipId?: string): OccupiedRange[] {
  return Object.values(track.clips ?? {})
    .filter((clip) => clip.id !== excludingClipId)
    .map((clip) => ({ clipId: clip.id, startUs: clip.timeline_start_us, endUs: clipEndUs(clip) }))
    .sort((a, b) => a.startUs - b.startUs || a.endUs - b.endUs || a.clipId.localeCompare(b.clipId));
}

export function rangesOverlap(startUs: number, endUs: number, range: OccupiedRange): boolean {
  return startUs < range.endUs && endUs > range.startUs;
}

export function canPlace(
  track: TimelineTrack,
  startUs: number,
  durationUs: number,
  excludingClipId?: string,
): boolean {
  const endUs = startUs + Math.max(1, durationUs);
  return startUs >= 0 && !occupiedRanges(track, excludingClipId).some((range) => rangesOverlap(startUs, endUs, range));
}

/**
 * Pick a universal layer that can hold an element at the exact requested
 * playhead position. The preferred layer is considered first, then the
 * remaining layers in visual order. No position shifting is performed.
 */
export function firstAvailableLayerAt(
  layers: TimelineTrack[],
  startUs: number,
  durationUs: number,
  preferredLayerId?: string | null,
): TimelineTrack | undefined {
  const preferred = preferredLayerId
    ? layers.find((layer) => layer.id === preferredLayerId)
    : undefined;
  if (preferred && canPlace(preferred, startUs, durationUs)) return preferred;
  return layers.find((layer) => layer.id !== preferred?.id && canPlace(layer, startUs, durationUs));
}

/**
 * Resolve a drag position to the closest valid free slot. Candidate positions
 * are every occupied edge plus the requested position, which is sufficient for
 * finding the nearest legal interval on a one-dimensional non-overlapping lane.
 */
export function nearestAvailableStart(
  track: TimelineTrack,
  proposedStartUs: number,
  durationUs: number,
  excludingClipId?: string,
): number {
  const proposed = Math.max(0, proposedStartUs);
  const ranges = occupiedRanges(track, excludingClipId);
  if (!ranges.some((range) => rangesOverlap(proposed, proposed + durationUs, range))) return proposed;

  const candidates = new Set<number>([0, proposed]);
  for (const range of ranges) {
    candidates.add(range.endUs);
    candidates.add(Math.max(0, range.startUs - durationUs));
  }
  const valid = [...candidates].filter((candidate) => (
    !ranges.some((range) => rangesOverlap(candidate, candidate + durationUs, range))
  ));
  if (valid.length === 0) return ranges.at(-1)?.endUs ?? 0;
  valid.sort((a, b) => Math.abs(a - proposed) - Math.abs(b - proposed) || a - b);
  return valid[0];
}

export function firstAvailableStartAtOrAfter(
  track: TimelineTrack,
  requestedStartUs: number,
  durationUs: number,
  excludingClipId?: string,
): number {
  let startUs = Math.max(0, requestedStartUs);
  for (const range of occupiedRanges(track, excludingClipId)) {
    if (startUs + durationUs <= range.startUs) return startUs;
    if (startUs < range.endUs) startUs = range.endUs;
  }
  return startUs;
}

export function previousOccupiedEnd(track: TimelineTrack, clip: TimelineClip): number {
  let endUs = 0;
  for (const range of occupiedRanges(track, clip.id)) {
    if (range.endUs <= clip.timeline_start_us) endUs = Math.max(endUs, range.endUs);
  }
  return endUs;
}

export function nextOccupiedStart(track: TimelineTrack, clip: TimelineClip): number | null {
  const endUs = clipEndUs(clip);
  const next = occupiedRanges(track, clip.id).find((range) => range.startUs >= endUs);
  return next?.startUs ?? null;
}

export function effectByAnchor(clip: TimelineClip, anchor: "START" | "END"): TimelineEffect | undefined {
  return (clip.effects ?? []).find((effect) => effect.type === "FADE" && effect.anchor === anchor && effect.enabled !== false);
}

export function fadeMultiplier(clip: TimelineClip, timelineTimeUs: number): number {
  const localUs = timelineTimeUs - clip.timeline_start_us;
  const durationUs = clipDurationUs(clip);
  let multiplier = 1;
  const fadeIn = effectByAnchor(clip, "START");
  if (fadeIn && fadeIn.duration_us > 0) multiplier *= Math.min(1, Math.max(0, localUs / fadeIn.duration_us));
  const fadeOut = effectByAnchor(clip, "END");
  if (fadeOut && fadeOut.duration_us > 0) multiplier *= Math.min(1, Math.max(0, (durationUs - localUs) / fadeOut.duration_us));
  return Math.min(1, Math.max(0, multiplier));
}
