"""Deterministic region tracking and layout-change detection.

Pure functions — no I/O, no imports of domain packages.  These operate
on lists of :class:`DetectedRegion` produced by the vision adapter and
build :class:`RegionTrack` objects with deterministic keyframe
propagation.

Tracking strategy
-----------------
Regions are matched across consecutive keyframes by **type + IoU**.  A
match is accepted when the IoU of the boxes exceeds ``match_iou``.
Matched regions extend the same track; unmatched regions start a new
track.  Between keyframes a track holds its last known box (deterministic
hold) — no interpolation, no prediction.  This makes the output
reproducible from the same keyframe results.

Layout changes
--------------
A layout change is flagged when the set of region types or their boxes
changes significantly between two consecutive keyframes (IoU drop below
``layout_change_threshold`` for any tracked region, or a region type
appears / disappears).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .schemas import (
    Box,
    Keyframe,
    LayoutChange,
    RegionTrack,
    RegionType,
    REGION_TYPES,
    VisualAnalysisValidationError,
)
from .vision import DetectedRegion


DEFAULT_MATCH_IOU = 0.3


# ---------------------------------------------------------------------------
# Strict validation of model output
# ---------------------------------------------------------------------------

def validate_detected_region(region: DetectedRegion) -> DetectedRegion:
    """Strictly validate a single detected region from the vision model.

    Raises :class:`VisualAnalysisValidationError` for:
    * unknown region type;
    * box outside the unit square (delegated to :class:`Box`);
    * confidence outside 0..1;
    * degenerate box (zero or negative width/height).

    Returns the validated region unchanged on success.
    """
    if region.type not in REGION_TYPES:
        raise VisualAnalysisValidationError(
            f"model returned unknown region type {region.type!r}; "
            f"expected one of {sorted(REGION_TYPES)}"
        )
    # Box validation happens in the Box constructor, but the DetectedRegion
    # was built from raw model output so we re-check here.
    box = region.box
    if box.width <= 0 or box.height <= 0:
        raise VisualAnalysisValidationError(
            f"model returned degenerate box (w={box.width}, h={box.height})"
        )
    if not (0.0 <= box.x <= 1.0 and 0.0 <= box.y <= 1.0):
        raise VisualAnalysisValidationError(
            f"model returned box with out-of-range origin ({box.x}, {box.y})"
        )
    if box.x + box.width > 1.0 + 1e-9 or box.y + box.height > 1.0 + 1e-9:
        raise VisualAnalysisValidationError(
            f"model returned box exceeding unit square "
            f"(x={box.x}, y={box.y}, w={box.width}, h={box.height})"
        )
    if not (0.0 <= region.confidence <= 1.0):
        raise VisualAnalysisValidationError(
            f"model returned confidence {region.confidence} outside [0, 1]"
        )
    return region


def validate_model_output(regions: list[DetectedRegion]) -> list[DetectedRegion]:
    """Validate a full keyframe worth of model output.

    Returns the validated list.  Raises on the first invalid region.
    """
    return [validate_detected_region(r) for r in regions]


# ---------------------------------------------------------------------------
# Deterministic tracking
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyframeResult:
    """A sampled keyframe with its detected regions."""

    time: float
    regions: list[DetectedRegion]


def _match_iou(a: Box, b: Box) -> float:
    return a.iou(b)


def track_regions(
    keyframe_results: list[KeyframeResult],
    *,
    start: float,
    end: float,
    match_iou: float = DEFAULT_MATCH_IOU,
) -> list[RegionTrack]:
    """Build deterministic region tracks from keyframe results.

    Each track gets a keyframe entry for every keyframe where it was
    observed.  Between keyframes the box is held constant (deterministic
    hold).  Track ids are assigned deterministically as
    ``{type}-{index}`` where index is the order of first appearance.
    """
    if not keyframe_results:
        return []
    # Sort keyframes by time for deterministic processing.
    sorted_kfs = sorted(keyframe_results, key=lambda k: k.time)

    # Active tracks: list of (type, last_box, keyframes_so_far, track_index)
    active: list[_ActiveTrack] = []
    # Counter per type for deterministic ids.
    type_counters: dict[str, int] = {}
    completed: list[RegionTrack] = []

    for kf in sorted_kfs:
        matched: set[int] = set()
        new_indices: set[int] = set()
        # Match detected regions to active tracks.
        for det_idx, det in enumerate(kf.regions):
            best_iou = 0.0
            best_track: Optional[int] = None
            for ti, track in enumerate(active):
                if ti in matched:
                    continue
                if track.type != det.type:
                    continue
                iou = _match_iou(track.last_box, det.box)
                if iou > best_iou and iou >= match_iou:
                    best_iou = iou
                    best_track = ti
            if best_track is not None:
                active[best_track].add_keyframe(kf.time, det)
                matched.add(best_track)
            else:
                # Start a new track.
                idx = type_counters.get(det.type, 0)
                type_counters[det.type] = idx + 1
                active.append(_ActiveTrack(
                    type=det.type,
                    track_id=f"{det.type.lower()}-{idx + 1}",
                    last_box=det.box,
                    keyframes=[Keyframe(time=kf.time, box=det.box, confidence=det.confidence)],
                ))
                new_indices.add(len(active) - 1)
        # Close tracks that existed before this keyframe and were not
        # matched.  Newly created tracks stay active.
        still_active: list[_ActiveTrack] = []
        for ti, track in enumerate(active):
            if ti in new_indices or ti in matched:
                still_active.append(track)
            else:
                completed.append(track.to_region_track(end))
        active = still_active

    # Close remaining active tracks at the end.
    for track in active:
        completed.append(track.to_region_track(end))

    # Sort by first keyframe time for deterministic output.
    completed.sort(key=lambda t: (t.keyframes[0].time if t.keyframes else 0.0, t.id))
    return completed


@dataclass
class _ActiveTrack:
    """Internal helper for :func:`track_regions`."""

    type: str
    track_id: str
    last_box: Box
    keyframes: list[Keyframe]

    def add_keyframe(self, time: float, det: DetectedRegion) -> None:
        self.keyframes.append(Keyframe(time=time, box=det.box, confidence=det.confidence))
        self.last_box = det.box

    def to_region_track(self, default_end: float) -> RegionTrack:
        start = self.keyframes[0].time if self.keyframes else 0.0
        end = self.keyframes[-1].time if self.keyframes else default_end
        return RegionTrack(
            id=self.track_id,
            type=self.type,
            start=start,
            end=max(end, start),
            keyframes=list(self.keyframes),
        )


# ---------------------------------------------------------------------------
# Layout change detection
# ---------------------------------------------------------------------------

def detect_layout_changes(
    keyframe_results: list[KeyframeResult],
    *,
    threshold: float = 0.3,
) -> list[LayoutChange]:
    """Detect significant layout changes between consecutive keyframes.

    A change is flagged when:
    * a region type appears or disappears between two keyframes;
    * the best-matching box for a type drops below ``threshold`` IoU.

    Returns a sorted list of :class:`LayoutChange`.
    """
    if len(keyframe_results) < 2:
        return []
    sorted_kfs = sorted(keyframe_results, key=lambda k: k.time)
    changes: list[LayoutChange] = []

    for prev, curr in zip(sorted_kfs, sorted_kfs[1:]):
        prev_by_type = _group_by_type(prev.regions)
        curr_by_type = _group_by_type(curr.regions)
        prev_types = set(prev_by_type.keys())
        curr_types = set(curr_by_type.keys())

        changed = False
        reasons: list[str] = []
        worst_iou = 1.0

        # Types that appeared or disappeared.
        appeared = curr_types - prev_types
        disappeared = prev_types - curr_types
        if appeared:
            changed = True
            reasons.append(f"regions appeared: {sorted(appeared)}")
        if disappeared:
            changed = True
            reasons.append(f"regions disappeared: {sorted(disappeared)}")

        # For types present in both, check IoU of best-matching boxes.
        for rtype in prev_types & curr_types:
            prev_boxes = prev_by_type[rtype]
            curr_boxes = curr_by_type[rtype]
            best = 0.0
            for pb in prev_boxes:
                for cb in curr_boxes:
                    iou = pb.iou(cb)
                    if iou > best:
                        best = iou
            if best < threshold:
                changed = True
                reasons.append(
                    f"{rtype} box shifted (IoU {best:.2f} < {threshold})"
                )
            if best < worst_iou:
                worst_iou = best

        if changed:
            changes.append(LayoutChange(
                time=curr.time,
                description="; ".join(reasons),
                confidence=min(1.0, max(0.0, 1.0 - worst_iou)),
            ))

    return changes


def _group_by_type(regions: list[DetectedRegion]) -> dict[str, list[Box]]:
    grouped: dict[str, list[Box]] = {}
    for r in regions:
        grouped.setdefault(r.type, []).append(r.box)
    return grouped


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------

def validate_template_against_keyframes(
    template_tracks: list[RegionTrack],
    keyframe_results: list[KeyframeResult],
    *,
    threshold: float = 0.3,
) -> tuple[bool, float]:
    """Validate a template's region tracks against sampled keyframes.

    Returns ``(ok, max_deviation)`` where ``max_deviation`` is the worst
    (1 - IoU) across all template regions and keyframes.  ``ok`` is True
    when ``max_deviation < threshold`` for every template region.
    """
    if not template_tracks or not keyframe_results:
        return True, 0.0

    max_deviation = 0.0
    for track in template_tracks:
        if not track.keyframes:
            continue
        template_box = track.keyframes[0].box
        for kf in keyframe_results:
            # Find the best-matching detected region of the same type.
            best_iou = 0.0
            for det in kf.regions:
                if det.type != track.type:
                    continue
                iou = template_box.iou(det.box)
                if iou > best_iou:
                    best_iou = iou
            deviation = 1.0 - best_iou
            if deviation > max_deviation:
                max_deviation = deviation
    ok = max_deviation < threshold
    return ok, max_deviation
