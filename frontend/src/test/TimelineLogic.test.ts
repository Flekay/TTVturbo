import { describe, expect, it } from "vitest";
import type { TimelineClip, TimelineTrack } from "../features/projects/api";
import {
  canPlace,
  fadeMultiplier,
  firstAvailableLayerAt,
  firstAvailableStartAtOrAfter,
  nearestAvailableStart,
  occupiedRanges,
} from "../features/projects/timelineLogic";

function clip(id: string, start: number, duration: number): TimelineClip {
  return {
    id,
    kind: "VIDEO",
    source_media_item_id: "media",
    source_start_us: 0,
    source_end_us: duration,
    timeline_start_us: start,
    speed: 1,
  };
}

function track(...clips: TimelineClip[]): TimelineTrack {
  return {
    id: "track",
    type: "UNIVERSAL",
    clips: Object.fromEntries(clips.map((item) => [item.id, item])),
  };
}

describe("universal timeline occupancy", () => {
  it("treats touching edges as valid but rejects any actual overlap", () => {
    const lane = track(clip("a", 0, 2_000_000), clip("b", 3_000_000, 2_000_000));
    expect(canPlace(lane, 2_000_000, 1_000_000)).toBe(true);
    expect(canPlace(lane, 1_999_999, 1_000_000)).toBe(false);
    expect(occupiedRanges(lane)).toEqual([
      { clipId: "a", startUs: 0, endUs: 2_000_000 },
      { clipId: "b", startUs: 3_000_000, endUs: 5_000_000 },
    ]);
  });

  it("uses the exact playhead in a free layer and otherwise requires a new layer", () => {
    const occupied = track(clip("a", 0, 5_000_000), clip("c", 8_000_000, 2_000_000));
    occupied.id = "occupied";
    const free = track(clip("b", 8_000_000, 1_000_000));
    free.id = "free";

    expect(firstAvailableLayerAt([occupied, free], 0, 2_000_000, "occupied")?.id).toBe("free");
    expect(firstAvailableLayerAt([occupied, free], 8_000_000, 2_000_000, "free")).toBeUndefined();
    expect(firstAvailableLayerAt([occupied, free], 5_000_000, 2_000_000, "occupied")?.id).toBe("occupied");
  });

  it("resolves drag positions to the closest legal interval", () => {
    const lane = track(clip("a", 0, 2_000_000), clip("b", 4_000_000, 2_000_000));
    expect(nearestAvailableStart(lane, 1_500_000, 1_000_000)).toBe(2_000_000);
    expect(nearestAvailableStart(lane, 3_700_000, 1_000_000)).toBe(3_000_000);
    expect(firstAvailableStartAtOrAfter(lane, 1_000_000, 3_000_000)).toBe(6_000_000);
  });

  it("combines attached fade-in and fade-out effects", () => {
    const item = clip("fade", 1_000_000, 4_000_000);
    item.effects = [
      { id: "in", type: "FADE", anchor: "START", duration_us: 1_000_000 },
      { id: "out", type: "FADE", anchor: "END", duration_us: 1_000_000 },
    ];
    expect(fadeMultiplier(item, 1_000_000)).toBe(0);
    expect(fadeMultiplier(item, 1_500_000)).toBeCloseTo(0.5);
    expect(fadeMultiplier(item, 3_000_000)).toBe(1);
    expect(fadeMultiplier(item, 4_500_000)).toBeCloseTo(0.5);
    expect(fadeMultiplier(item, 5_000_000)).toBe(0);
  });
});
