"""Runtime migration from legacy typed/overlapping tracks to universal lanes."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from .operations import canonical_json

_MEDIA_TRACK_TYPES = {"VIDEO", "AUDIO", "GAMEPLAY", "FACECAM", "UNIVERSAL"}


def _duration_us(clip: dict[str, Any]) -> int:
    speed = max(0.05, float(clip.get("speed") or 1.0))
    source_start = int(clip.get("source_start_us") or 0)
    source_end = int(clip.get("source_end_us") or source_start + 1)
    return max(1, int(round((source_end - source_start) / speed)))


def _end_us(clip: dict[str, Any]) -> int:
    return int(clip.get("timeline_start_us") or 0) + _duration_us(clip)


def _legacy_kind(track_type: str) -> str:
    return "AUDIO" if track_type == "AUDIO" else "VIDEO"


def _lane_track_id(base_track_id: str, lane_index: int, used_ids: set[str]) -> str:
    digest = hashlib.sha256(f"{base_track_id}:{lane_index}".encode("utf-8")).hexdigest()[:10]
    suffix = f"-lane-{lane_index + 1}-{digest}"
    candidate = f"{base_track_id[: max(1, 128 - len(suffix))]}{suffix}"
    collision = 0
    while candidate in used_ids:
        collision += 1
        collision_suffix = f"-{collision}"
        candidate = f"{candidate[: max(1, 128 - len(collision_suffix))]}{collision_suffix}"
    used_ids.add(candidate)
    return candidate


def _ordered_track_ids(sequence: dict[str, Any]) -> list[str]:
    tracks = sequence.get("tracks") or {}
    result: list[str] = []
    seen: set[str] = set()
    for track_id in sequence.get("track_order") or []:
        if track_id in tracks and track_id not in seen:
            result.append(track_id)
            seen.add(track_id)
    for track_id in sorted(tracks):
        if track_id not in seen:
            result.append(track_id)
    return result


def migrate_universal_timeline_state(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a deterministic, non-overlapping universal-track representation.

    Historical commits are reconstructed and hash-checked in their exact legacy
    shape first. This migration is then applied to the runtime state. On the
    first subsequent edit, the service stores the migrated state as an internal
    patch, so all later commits remain fully reproducible.
    """
    before = canonical_json(state)
    migrated = copy.deepcopy(state)
    migrated["schema_version"] = max(2, int(migrated.get("schema_version") or 1))

    for sequence in (migrated.get("sequences") or {}).values():
        original_tracks = sequence.get("tracks") or {}
        used_ids = set(original_tracks)
        rebuilt_tracks: dict[str, dict[str, Any]] = {}
        rebuilt_order: list[str] = []

        for track_id in _ordered_track_ids(sequence):
            original = copy.deepcopy(original_tracks[track_id])
            original_type = str(original.get("type") or "UNIVERSAL").upper()
            clips = original.get("clips") or {}
            clip_order = [clip_id for clip_id in (original.get("clip_order") or []) if clip_id in clips]
            clip_order.extend(sorted(clip_id for clip_id in clips if clip_id not in set(clip_order)))
            order_index = {clip_id: index for index, clip_id in enumerate(clip_order)}

            for clip_id, clip in clips.items():
                clip.setdefault("id", clip_id)
                if str(clip.get("kind") or "").upper() not in {"VIDEO", "AUDIO", "IMAGE", "TEXT"}:
                    clip["kind"] = _legacy_kind(original_type)

            # Non-media legacy tracks keep their specialized semantics. Media
            # tracks become universal regardless of the element kind they hold.
            universal = original_type in _MEDIA_TRACK_TYPES or bool(clips)
            target_type = "UNIVERSAL" if universal else original_type

            base = copy.deepcopy(original)
            base["id"] = track_id
            base["type"] = target_type
            base["clips"] = {}
            base["clip_order"] = []
            base.setdefault("captions", {})
            base.setdefault("caption_order", [])
            base.setdefault("properties", {})

            lanes: list[dict[str, Any]] = [base]
            lane_ends: list[int] = [0]
            sorted_clips = sorted(
                clips.values(),
                key=lambda clip: (
                    int(clip.get("timeline_start_us") or 0),
                    order_index.get(str(clip.get("id") or ""), 10**9),
                    str(clip.get("id") or ""),
                ),
            )

            for clip in sorted_clips:
                start_us = max(0, int(clip.get("timeline_start_us") or 0))
                clip["timeline_start_us"] = start_us
                lane_index = next((index for index, end_us in enumerate(lane_ends) if end_us <= start_us), None)
                if lane_index is None:
                    lane_index = len(lanes)
                    overflow = copy.deepcopy(base)
                    overflow_id = _lane_track_id(track_id, lane_index, used_ids)
                    overflow["id"] = overflow_id
                    overflow["name"] = f"{str(base.get('name') or 'Spur')} {lane_index + 1}"
                    overflow["clips"] = {}
                    overflow["clip_order"] = []
                    # Captions/overlay metadata belongs only to the original lane.
                    overflow["captions"] = {}
                    overflow["caption_order"] = []
                    lanes.append(overflow)
                    lane_ends.append(0)
                clip_id = str(clip["id"])
                lanes[lane_index]["clips"][clip_id] = clip
                lanes[lane_index]["clip_order"].append(clip_id)
                lane_ends[lane_index] = _end_us(clip)

            for lane in lanes:
                rebuilt_tracks[lane["id"]] = lane
                rebuilt_order.append(lane["id"])

        sequence["tracks"] = rebuilt_tracks
        sequence["track_order"] = rebuilt_order

    return migrated, canonical_json(migrated) != before
