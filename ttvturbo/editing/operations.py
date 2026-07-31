"""Deterministic, reversible edit-operation engine."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from .errors import EditValidationError
from .schemas import (
    ALL_OPERATION_TYPES,
    INTERNAL_OPERATION_TYPES,
    MAX_DIMENSION,
    MAX_FPS_DENOMINATOR,
    MAX_FPS_NUMERATOR,
    MIN_DIMENSION,
    PUBLIC_OPERATION_TYPES,
    TrackType,
    FormatProfile,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def empty_state(project_id: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_id": project_id,
        "sources": copy.deepcopy(sources),
        "sequences": {},
    }



_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _identifier(value: Any, name: str) -> str:
    result = str(value or "")
    if not _ID_RE.fullmatch(result):
        raise EditValidationError(f"{name} must contain only letters, numbers, '_' or '-' and be at most 128 characters")
    return result

def _int_us(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EditValidationError(f"{name} must be an integer number of microseconds")
    if value < minimum:
        raise EditValidationError(f"{name} must be >= {minimum}")
    return value


def _number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditValidationError(f"{name} must be numeric")
    result = float(value)
    if minimum is not None and result < minimum:
        raise EditValidationError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise EditValidationError(f"{name} must be <= {maximum}")
    return result


def validate_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
    seq = copy.deepcopy(sequence)
    required = ("id", "name", "width", "height", "fps_numerator", "fps_denominator", "format_profile")
    for field in required:
        if field not in seq:
            raise EditValidationError(f"sequence missing {field}")
    seq["id"] = _identifier(seq["id"], "sequence id")
    if not str(seq["name"]).strip():
        raise EditValidationError("sequence name must not be empty")
    width = int(seq["width"])
    height = int(seq["height"])
    if not MIN_DIMENSION <= width <= MAX_DIMENSION or not MIN_DIMENSION <= height <= MAX_DIMENSION:
        raise EditValidationError(f"sequence dimensions must be {MIN_DIMENSION}..{MAX_DIMENSION}")
    fps_n = int(seq["fps_numerator"])
    fps_d = int(seq["fps_denominator"])
    if fps_n <= 0 or fps_n > MAX_FPS_NUMERATOR or fps_d <= 0 or fps_d > MAX_FPS_DENOMINATOR:
        raise EditValidationError("invalid rational fps")
    if seq["format_profile"] not in {p.value for p in FormatProfile}:
        raise EditValidationError(f"unknown format profile: {seq['format_profile']}")
    seq.update({"width": width, "height": height, "fps_numerator": fps_n, "fps_denominator": fps_d})
    safe_area_enabled = bool(seq.get("safe_area_enabled", True))
    max_h = width // 2
    max_v = height // 2
    safe_area_margin_top = int(seq.get("safe_area_margin_top", 80))
    safe_area_margin_right = int(seq.get("safe_area_margin_right", 80))
    safe_area_margin_bottom = int(seq.get("safe_area_margin_bottom", 80))
    safe_area_margin_left = int(seq.get("safe_area_margin_left", 80))
    for name, value, maximum in (
        ("safe_area_margin_top", safe_area_margin_top, max_v),
        ("safe_area_margin_right", safe_area_margin_right, max_h),
        ("safe_area_margin_bottom", safe_area_margin_bottom, max_v),
        ("safe_area_margin_left", safe_area_margin_left, max_h),
    ):
        if value < 0 or value > maximum:
            raise EditValidationError(f"{name} must be between 0 and {maximum} px")
    seq["safe_area_enabled"] = safe_area_enabled
    seq["safe_area_margin_top"] = safe_area_margin_top
    seq["safe_area_margin_right"] = safe_area_margin_right
    seq["safe_area_margin_bottom"] = safe_area_margin_bottom
    seq["safe_area_margin_left"] = safe_area_margin_left
    seq.setdefault("tracks", {})
    seq.setdefault("track_order", [])
    seq.setdefault("layout", None)
    return seq


def _sequence(state: dict[str, Any], sequence_id: str) -> dict[str, Any]:
    try:
        return state["sequences"][sequence_id]
    except KeyError as exc:
        raise EditValidationError(f"unknown sequence: {sequence_id}") from exc


def _track(seq: dict[str, Any], track_id: str) -> dict[str, Any]:
    try:
        return seq["tracks"][track_id]
    except KeyError as exc:
        raise EditValidationError(f"unknown layer: {track_id}") from exc


def _clip(track: dict[str, Any], clip_id: str) -> dict[str, Any]:
    try:
        return track["clips"][clip_id]
    except KeyError as exc:
        raise EditValidationError(f"unknown clip: {clip_id}") from exc


ELEMENT_KINDS = {"VIDEO", "AUDIO", "IMAGE", "TEXT"}
EFFECT_ANCHORS = {"START", "END"}


def _clip_duration_us(clip: dict[str, Any]) -> int:
    speed = max(0.05, float(clip.get("speed") or 1.0))
    return max(1, int(round((int(clip["source_end_us"]) - int(clip["source_start_us"])) / speed)))


def _clip_end_us(clip: dict[str, Any]) -> int:
    return int(clip["timeline_start_us"]) + _clip_duration_us(clip)


def _refresh_occupied_ranges(track: dict[str, Any]) -> None:
    # Occupancy is derived from clips on every validation. It must not become
    # part of the persisted edit state, otherwise historical commit hashes from
    # projects created before universal tracks would change during replay.
    track.pop("occupied_ranges", None)


def _assert_track_slot_available(track: dict[str, Any], candidate: dict[str, Any], *, excluding_clip_id: str | None = None) -> None:
    start = int(candidate["timeline_start_us"])
    end = _clip_end_us(candidate)
    for other in (track.get("clips") or {}).values():
        if other.get("id") == excluding_clip_id:
            continue
        other_start = int(other["timeline_start_us"])
        other_end = _clip_end_us(other)
        if start < other_end and end > other_start:
            raise EditValidationError(
                f"timeline overlap on layer {track.get('id')}: {candidate.get('id')} [{start},{end}) conflicts with {other.get('id')} [{other_start},{other_end})"
            )


def _first_available_start_at_or_after(
    track: dict[str, Any],
    requested_start_us: int,
    duration_us: int,
    *,
    excluding_clip_id: str | None = None,
) -> int:
    """Return the first free half-open interval on a track.

    This is the authoritative server-side placement path. The browser may be
    displaying a slightly stale project state while another commit is being
    attached, so automatic insertions must never rely only on client-side
    occupancy calculations.
    """
    start_us = max(0, int(requested_start_us))
    ranges: list[tuple[int, int, str]] = []
    for other in (track.get("clips") or {}).values():
        if other.get("id") == excluding_clip_id:
            continue
        ranges.append((int(other["timeline_start_us"]), _clip_end_us(other), str(other.get("id") or "")))
    ranges.sort(key=lambda item: (item[0], item[1], item[2]))
    for other_start, other_end, _ in ranges:
        if start_us + duration_us <= other_start:
            return start_us
        if start_us < other_end:
            start_us = other_end
    return start_us


def _normalize_effect(effect: dict[str, Any], clip_duration_us: int) -> dict[str, Any]:
    result = copy.deepcopy(effect)
    result["id"] = _identifier(result.get("id"), "effect id")
    effect_type = str(result.get("type") or "").upper()
    if not effect_type:
        raise EditValidationError("effect type must not be empty")
    result["type"] = effect_type
    anchor = str(result.get("anchor") or "START").upper()
    if anchor not in EFFECT_ANCHORS:
        raise EditValidationError(f"unknown effect anchor: {anchor}")
    result["anchor"] = anchor
    duration = _int_us(result.get("duration_us"), "effect.duration_us", minimum=1)
    if duration > clip_duration_us:
        raise EditValidationError("effect duration must not exceed element duration")
    result["duration_us"] = duration
    result["enabled"] = bool(result.get("enabled", True))
    parameters = result.get("parameters", {})
    if not isinstance(parameters, dict):
        raise EditValidationError("effect parameters must be an object")
    result["parameters"] = copy.deepcopy(parameters)
    return result


def _normalize_text(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EditValidationError("text must be an object")
    result = copy.deepcopy(value)
    content = str(result.get("content") or "").strip()
    if not content:
        raise EditValidationError("text content must not be empty")
    if len(content) > 10000:
        raise EditValidationError("text content is too long")
    result["content"] = content
    if "font_size" in result:
        result["font_size"] = _number(result["font_size"], "text.font_size", minimum=1, maximum=1000)
    if "align" in result and result["align"] not in {"left", "center", "right"}:
        raise EditValidationError("text.align must be left, center or right")
    return result


def _legacy_kind_for_track(track_type: str) -> str:
    if track_type == TrackType.AUDIO.value:
        return "AUDIO"
    return "VIDEO"


def _normalize_track(track: dict[str, Any], *, validate_overlaps: bool = True) -> dict[str, Any]:
    result = copy.deepcopy(track)
    for field in ("id", "type", "name"):
        if field not in result:
            raise EditValidationError(f"layer missing {field}")
    result["id"] = _identifier(result["id"], "layer id")
    if result["type"] not in {t.value for t in TrackType}:
        raise EditValidationError(f"unknown layer type: {result['type']}")
    result.setdefault("clips", {})
    result.setdefault("clip_order", [])
    result.setdefault("captions", {})
    result.setdefault("caption_order", [])
    result.setdefault("properties", {})
    if not isinstance(result["clips"], dict):
        raise EditValidationError("layer clips must be an object")
    for clip_id, clip in result["clips"].items():
        if not isinstance(clip, dict):
            raise EditValidationError("layer clip must be an object")
        if str(clip.get("id") or clip_id) != str(clip_id):
            raise EditValidationError("clip map key must match clip id")
        if validate_overlaps:
            _assert_track_slot_available(result, clip, excluding_clip_id=str(clip_id))
    _refresh_occupied_ranges(result)
    return result


def _normalize_clip(clip: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(clip)
    for field in ("id", "source_start_us", "source_end_us", "timeline_start_us"):
        if field not in result:
            raise EditValidationError(f"clip missing {field}")
    result["id"] = _identifier(result["id"], "clip id")
    kind_was_provided = "kind" in result
    kind = str(result.get("kind") or "VIDEO").upper()
    if kind not in ELEMENT_KINDS:
        raise EditValidationError(f"unknown element kind: {kind}")
    if kind_was_provided:
        result["kind"] = kind
    else:
        result.pop("kind", None)
    source_media_item_id = str(result.get("source_media_item_id") or "")
    if kind != "TEXT" and not source_media_item_id:
        raise EditValidationError("media elements require source_media_item_id")
    result["source_media_item_id"] = source_media_item_id
    start = _int_us(result["source_start_us"], "source_start_us")
    end = _int_us(result["source_end_us"], "source_end_us")
    timeline = _int_us(result["timeline_start_us"], "timeline_start_us")
    if end <= start:
        raise EditValidationError("source_end_us must be greater than source_start_us")
    result.update({"source_start_us": start, "source_end_us": end, "timeline_start_us": timeline})
    result.setdefault("transform", {"x": 0.0, "y": 0.0, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0.0})
    result.setdefault("crop", {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0})
    result.setdefault("opacity", 1.0)
    result.setdefault("speed", 1.0)
    result.setdefault("audio_gain", 1.0)
    result.setdefault("audio_muted", False)
    result.setdefault("facecam_variant", None)
    result.setdefault("audio_fades", [])
    if kind == "TEXT":
        result["text"] = _normalize_text(result.get("text") or {})
    effects_were_provided = "effects" in result
    effects = result.get("effects") or []
    if not isinstance(effects, list):
        raise EditValidationError("effects must be a list")
    duration = _clip_duration_us(result)
    normalized_effects = [_normalize_effect(effect, duration) for effect in effects]
    ids = [effect["id"] for effect in normalized_effects]
    if len(ids) != len(set(ids)):
        raise EditValidationError("effect ids must be unique per element")
    if effects_were_provided:
        result["effects"] = normalized_effects
    else:
        result.pop("effects", None)
    return result


def _normalized_box(value: dict[str, Any], name: str) -> dict[str, float]:
    result = {}
    for key in ("x", "y", "width", "height"):
        result[key] = _number(value.get(key), f"{name}.{key}", minimum=0.0, maximum=1.0)
    if result["width"] <= 0 or result["height"] <= 0:
        raise EditValidationError(f"{name} width and height must be > 0")
    if result["x"] + result["width"] > 1.000001 or result["y"] + result["height"] > 1.000001:
        raise EditValidationError(f"{name} must remain inside normalized frame")
    return result


class OperationEngine:
    """Apply validated operations and compute their inverse payloads."""

    def apply(
        self,
        state: dict[str, Any],
        operation: dict[str, Any],
        *,
        allow_internal: bool = False,
        validate_timeline_overlaps: bool = True,
    ) -> dict[str, Any]:
        op_type = str(operation.get("type") or "")
        valid = ALL_OPERATION_TYPES if allow_internal else PUBLIC_OPERATION_TYPES
        if op_type not in valid:
            raise EditValidationError(f"unsupported operation type: {op_type}")
        payload = copy.deepcopy(operation.get("payload") or {})
        sequence_id = operation.get("sequence_id") or payload.get("sequence_id")
        entity_id = operation.get("entity_id")
        before = copy.deepcopy(state)

        if op_type == "APPLY_STATE_PATCH":
            replacement = payload.get("state")
            if not isinstance(replacement, dict):
                raise EditValidationError("APPLY_STATE_PATCH requires state")
            state.clear(); state.update(copy.deepcopy(replacement))
            up = {"state": copy.deepcopy(replacement)}
            down = {"state": before}
            return self._record(op_type, sequence_id, entity_id, up, down, "$", None, None)

        if op_type == "ADD_SOURCE":
            source = copy.deepcopy(payload.get("source") or payload)
            for field in ("id", "media_item_id", "sha256", "created_at"):
                if not source.get(field):
                    raise EditValidationError(f"source missing {field}")
            source["id"] = _identifier(source["id"], "source id")
            source["media_item_id"] = str(source["media_item_id"])
            source["asset_id"] = source.get("asset_id")
            source["source_revision"] = source.get("source_revision")
            source_key = (source["media_item_id"], source.get("asset_id"))
            existing_keys = {(str(item.get("media_item_id")), item.get("asset_id")) for item in state.get("sources", [])}
            if source_key in existing_keys or (source_key[0], None) in existing_keys:
                raise EditValidationError("project source already exists")
            state.setdefault("sources", []).append(source)
            return self._record(
                op_type, None, source["id"], {"source": source}, {"source_id": source["id"]},
                f"sources.{source['id']}",
            )

        if op_type == "CREATE_SEQUENCE":
            seq = validate_sequence(payload.get("sequence") or payload)
            if seq["id"] in state["sequences"]:
                raise EditValidationError(f"sequence already exists: {seq['id']}")
            state["sequences"][seq["id"]] = seq
            return self._record(op_type, seq["id"], seq["id"], {"sequence": seq}, {"sequence_id": seq["id"]}, f"sequences.{seq['id']}")

        if op_type == "DELETE_SEQUENCE":
            sid = str(sequence_id or payload.get("sequence_id") or "")
            seq = copy.deepcopy(_sequence(state, sid))
            if len(state["sequences"]) <= 1:
                raise EditValidationError("an edit project must keep at least one sequence")
            del state["sequences"][sid]
            return self._record(op_type, sid, sid, {"sequence_id": sid}, {"sequence": seq}, f"sequences.{sid}")

        sid = str(sequence_id or "")
        seq = _sequence(state, sid)

        if op_type == "SET_SEQUENCE_FORMAT":
            format_keys = ("width", "height", "fps_numerator", "fps_denominator", "format_profile")
            safe_keys = ("safe_area_enabled", "safe_area_margin_top", "safe_area_margin_right", "safe_area_margin_bottom", "safe_area_margin_left")
            old = {k: seq.get(k) for k in format_keys + safe_keys}
            merged = dict(old); merged.update(payload)
            checked = validate_sequence({"id": sid, "name": seq["name"], **merged})
            new = {k: checked[k] for k in format_keys + safe_keys}
            seq.update(new)
            return self._record(op_type, sid, sid, new, old, f"sequences.{sid}.format")

        if op_type == "ADD_TRACK":
            track = _normalize_track(payload.get("track") or payload, validate_overlaps=validate_timeline_overlaps)
            tid = track["id"]
            if tid in seq["tracks"]:
                raise EditValidationError(f"layer already exists: {tid}")
            seq["tracks"][tid] = track
            index = payload.get("index")
            if index is None: seq["track_order"].append(tid)
            else: seq["track_order"].insert(max(0, min(int(index), len(seq["track_order"]))), tid)
            return self._record(op_type, sid, tid, {"track": track, "index": seq["track_order"].index(tid)}, {"track_id": tid}, f"sequences.{sid}.tracks.{tid}")

        if op_type == "REMOVE_TRACK":
            tid = str(payload.get("track_id") or entity_id or "")
            track = copy.deepcopy(_track(seq, tid)); index = seq["track_order"].index(tid)
            del seq["tracks"][tid]; seq["track_order"].remove(tid)
            return self._record(op_type, sid, tid, {"track_id": tid}, {"track": track, "index": index}, f"sequences.{sid}.tracks.{tid}")

        if op_type == "RENAME_TRACK":
            tid = str(payload.get("track_id") or entity_id or "")
            track = _track(seq, tid)
            name = str(payload.get("name") or "").strip()
            if not name:
                raise EditValidationError("layer name must not be empty")
            if len(name) > 120:
                raise EditValidationError("layer name must be at most 120 characters")
            old_name = str(track.get("name") or "")
            track["name"] = name
            return self._record(
                op_type,
                sid,
                tid,
                {"track_id": tid, "name": name},
                {"track_id": tid, "name": old_name},
                f"sequences.{sid}.tracks.{tid}.name",
            )

        if op_type == "REORDER_TRACK":
            order = list(payload.get("order") or [])
            if set(order) != set(seq["tracks"].keys()) or len(order) != len(seq["tracks"]):
                raise EditValidationError("layer order must contain every layer exactly once")
            old = list(seq["track_order"]); seq["track_order"] = order
            return self._record(op_type, sid, None, {"order": order}, {"order": old}, f"sequences.{sid}.track_order")

        if op_type == "SET_LAYOUT":
            old = copy.deepcopy(seq.get("layout")); layout = copy.deepcopy(payload.get("layout"))
            seq["layout"] = layout
            return self._record(op_type, sid, sid, {"layout":layout}, {"layout":old}, f"sequences.{sid}.layout")

        tid = str(payload.get("track_id") or "")
        track = _track(seq, tid)

        if op_type == "ADD_CLIP":
            raw_clip = copy.deepcopy(payload.get("clip") or {})
            clip = _normalize_clip(raw_clip)
            clip_kind = str(clip.get("kind") or _legacy_kind_for_track(str(track.get("type") or ""))).upper()
            if clip_kind != "TEXT":
                source_keys = {(str(src.get("media_item_id")), src.get("asset_id")) for src in state.get("sources", [])}
                clip_source = (str(clip.get("source_media_item_id")), clip.get("source_asset_id"))
                if clip_source not in source_keys and (clip_source[0], None) not in source_keys:
                    raise EditValidationError("clip must reference an immutable project source")
            cid = clip["id"]
            if cid in track["clips"]:
                raise EditValidationError(f"clip already exists: {cid}")
            placement = str(payload.get("placement") or "EXACT").upper()
            if placement not in {"EXACT", "NEXT_AVAILABLE"}:
                raise EditValidationError(f"unknown clip placement mode: {placement}")
            if placement == "NEXT_AVAILABLE":
                clip["timeline_start_us"] = _first_available_start_at_or_after(
                    track,
                    int(clip["timeline_start_us"]),
                    _clip_duration_us(clip),
                )
            elif validate_timeline_overlaps:
                _assert_track_slot_available(track, clip)
            track["clips"][cid] = clip
            index = payload.get("index")
            if index is None: track["clip_order"].append(cid)
            else: track["clip_order"].insert(max(0, min(int(index), len(track["clip_order"]))), cid)
            _refresh_occupied_ranges(track)
            return self._record(op_type, sid, cid, {"track_id": tid, "clip": clip, "index": track["clip_order"].index(cid)}, {"track_id": tid, "clip_id": cid}, f"sequences.{sid}.tracks.{tid}.clips.{cid}", clip["timeline_start_us"], _clip_end_us(clip))

        if op_type == "REMOVE_CLIP":
            cid = str(payload.get("clip_id") or entity_id or "")
            clip = copy.deepcopy(_clip(track, cid)); index = track["clip_order"].index(cid)
            del track["clips"][cid]; track["clip_order"].remove(cid)
            _refresh_occupied_ranges(track)
            return self._record(op_type, sid, cid, {"track_id": tid, "clip_id": cid}, {"track_id": tid, "clip": clip, "index": index}, f"sequences.{sid}.tracks.{tid}.clips.{cid}")

        cid = str(payload.get("clip_id") or entity_id or "")
        clip = _clip(track, cid)

        if op_type == "MOVE_CLIP":
            old = {"track_id": tid, "clip_id": cid, "timeline_start_us": clip["timeline_start_us"]}
            value = _int_us(payload.get("timeline_start_us"), "timeline_start_us")
            candidate = copy.deepcopy(clip); candidate["timeline_start_us"] = value
            if validate_timeline_overlaps:
                _assert_track_slot_available(track, candidate, excluding_clip_id=cid)
            clip["timeline_start_us"] = value
            _refresh_occupied_ranges(track)
            return self._record(op_type, sid, cid, {"track_id": tid, "clip_id": cid, "timeline_start_us": value}, old, f"sequences.{sid}.tracks.{tid}.clips.{cid}.timeline_start_us", value, value + _clip_duration_us(clip))

        if op_type == "TRIM_CLIP":
            old = {"track_id": tid, "clip_id": cid, "source_start_us": clip["source_start_us"], "source_end_us": clip["source_end_us"]}
            start = _int_us(payload.get("source_start_us", clip["source_start_us"]), "source_start_us")
            end = _int_us(payload.get("source_end_us", clip["source_end_us"]), "source_end_us")
            if end <= start: raise EditValidationError("source_end_us must be greater than source_start_us")
            candidate = copy.deepcopy(clip); candidate["source_start_us"] = start; candidate["source_end_us"] = end
            if validate_timeline_overlaps:
                _assert_track_slot_available(track, candidate, excluding_clip_id=cid)
            clip["source_start_us"] = start; clip["source_end_us"] = end
            for effect in clip.get("effects", []):
                effect["duration_us"] = min(effect["duration_us"], _clip_duration_us(clip))
            _refresh_occupied_ranges(track)
            up = {"track_id": tid, "clip_id": cid, "source_start_us": start, "source_end_us": end}
            return self._record(op_type, sid, cid, up, old, f"sequences.{sid}.tracks.{tid}.clips.{cid}.trim", clip["timeline_start_us"], _clip_end_us(clip))

        if op_type == "SPLIT_CLIP":
            split = _int_us(payload.get("split_source_us"), "split_source_us")
            if not clip["source_start_us"] < split < clip["source_end_us"]:
                raise EditValidationError("split_source_us must be inside clip")
            left_id = _identifier(payload.get("left_clip_id"), "left clip id"); right_id = _identifier(payload.get("right_clip_id"), "right clip id")
            if not left_id or not right_id or left_id == right_id or left_id in track["clips"] or right_id in track["clips"]:
                raise EditValidationError("split requires two new unique clip ids")
            original = copy.deepcopy(clip); index = track["clip_order"].index(cid)
            left = copy.deepcopy(clip); left.update({"id": left_id, "source_end_us": split})
            speed = max(0.05, float(clip.get("speed") or 1.0))
            right = copy.deepcopy(clip); right.update({"id": right_id, "source_start_us": split, "timeline_start_us": clip["timeline_start_us"] + int(round((split-clip["source_start_us"]) / speed))})
            if "effects" in clip:
                left["effects"] = [copy.deepcopy(effect) for effect in clip.get("effects", []) if effect.get("anchor") != "END"]
                right["effects"] = [copy.deepcopy(effect) for effect in clip.get("effects", []) if effect.get("anchor") != "START"]
                for part in (left, right):
                    for effect in part.get("effects", []):
                        effect["duration_us"] = min(effect["duration_us"], _clip_duration_us(part))
            del track["clips"][cid]; track["clips"][left_id] = left; track["clips"][right_id] = right
            track["clip_order"][index:index+1] = [left_id, right_id]
            _refresh_occupied_ranges(track)
            up = {"track_id": tid, "clip_id": cid, "split_source_us": split, "left_clip_id": left_id, "right_clip_id": right_id}
            down = {"track_id": tid, "original_clip": original, "left_clip_id": left_id, "right_clip_id": right_id, "index": index}
            return self._record(op_type, sid, cid, up, down, f"sequences.{sid}.tracks.{tid}.clips.{cid}.split")

        if op_type in {"SET_TRANSFORM", "SET_CROP", "SET_OPACITY", "SET_SPEED", "SET_AUDIO_GAIN", "SET_AUDIO_MUTE", "SET_FACECAM_VARIANT"}:
            key = {
                "SET_TRANSFORM":"transform", "SET_CROP":"crop", "SET_OPACITY":"opacity", "SET_SPEED":"speed",
                "SET_AUDIO_GAIN":"audio_gain", "SET_AUDIO_MUTE":"audio_muted", "SET_FACECAM_VARIANT":"facecam_variant",
            }[op_type]
            old_value = copy.deepcopy(clip.get(key))
            value = payload.get("value")
            if op_type == "SET_CROP": value = _normalized_box(value or {}, "crop")
            elif op_type == "SET_TRANSFORM":
                if not isinstance(value, dict): raise EditValidationError("transform must be an object")
                value = {k: _number(value.get(k, d), f"transform.{k}") for k,d in {"x":0.0,"y":0.0,"scale_x":1.0,"scale_y":1.0,"rotation":0.0}.items()}
                if value["scale_x"] <= 0 or value["scale_y"] <= 0: raise EditValidationError("transform scale must be > 0")
            elif op_type == "SET_OPACITY": value = _number(value, "opacity", minimum=0.0, maximum=1.0)
            elif op_type == "SET_SPEED": value = _number(value, "speed", minimum=0.05, maximum=16.0)
            elif op_type == "SET_AUDIO_GAIN": value = _number(value, "audio_gain", minimum=0.0, maximum=8.0)
            elif op_type == "SET_AUDIO_MUTE": value = bool(value)
            elif op_type == "SET_FACECAM_VARIANT" and value is not None and not isinstance(value, str): raise EditValidationError("facecam variant must be a string or null")
            if op_type == "SET_SPEED":
                candidate = copy.deepcopy(clip); candidate[key] = value
                if validate_timeline_overlaps:
                    _assert_track_slot_available(track, candidate, excluding_clip_id=cid)
            clip[key] = copy.deepcopy(value)
            if op_type == "SET_SPEED":
                for effect in clip.get("effects", []):
                    effect["duration_us"] = min(effect["duration_us"], _clip_duration_us(clip))
                _refresh_occupied_ranges(track)
            up = {"track_id": tid, "clip_id": cid, "value": value}
            down = {"track_id": tid, "clip_id": cid, "value": old_value}
            return self._record(op_type, sid, cid, up, down, f"sequences.{sid}.tracks.{tid}.clips.{cid}.{key}")

        if op_type == "SET_TEXT":
            if clip.get("kind") != "TEXT":
                raise EditValidationError("SET_TEXT requires a TEXT element")
            old_value = copy.deepcopy(clip.get("text"))
            value = _normalize_text(payload.get("value"))
            clip["text"] = value
            return self._record(op_type, sid, cid, {"track_id":tid,"clip_id":cid,"value":value}, {"track_id":tid,"clip_id":cid,"value":old_value}, f"sequences.{sid}.tracks.{tid}.clips.{cid}.text")

        if op_type == "ADD_EFFECT":
            effect = _normalize_effect(payload.get("effect") or {}, _clip_duration_us(clip))
            if any(existing.get("id") == effect["id"] for existing in clip.get("effects", [])):
                raise EditValidationError("effect already exists")
            if effect["type"] == "FADE" and any(existing.get("type") == "FADE" and existing.get("anchor") == effect["anchor"] for existing in clip.get("effects", [])):
                raise EditValidationError(f"element already has a fade at {effect['anchor']}")
            clip.setdefault("effects", []).append(effect)
            return self._record(op_type, sid, effect["id"], {"track_id":tid,"clip_id":cid,"effect":effect}, {"track_id":tid,"clip_id":cid,"effect_id":effect["id"]}, f"sequences.{sid}.tracks.{tid}.clips.{cid}.effects.{effect['id']}")

        if op_type == "UPDATE_EFFECT":
            effect_id = str(payload.get("effect_id") or entity_id or "")
            effects = clip.setdefault("effects", [])
            index = next((i for i, effect in enumerate(effects) if effect.get("id") == effect_id), None)
            if index is None:
                raise EditValidationError(f"unknown effect: {effect_id}")
            old_effect = copy.deepcopy(effects[index])
            merged = copy.deepcopy(old_effect); merged.update(copy.deepcopy(payload.get("updates") or {})); merged["id"] = effect_id
            effect = _normalize_effect(merged, _clip_duration_us(clip))
            if effect["type"] == "FADE" and any(
                i != index and existing.get("type") == "FADE" and existing.get("anchor") == effect["anchor"]
                for i, existing in enumerate(effects)
            ):
                raise EditValidationError(f"element already has a fade at {effect['anchor']}")
            effects[index] = effect
            return self._record(op_type, sid, effect_id, {"track_id":tid,"clip_id":cid,"effect_id":effect_id,"updates":effect}, {"track_id":tid,"clip_id":cid,"effect_id":effect_id,"updates":old_effect}, f"sequences.{sid}.tracks.{tid}.clips.{cid}.effects.{effect_id}")

        if op_type == "REMOVE_EFFECT":
            effect_id = str(payload.get("effect_id") or entity_id or "")
            effects = clip.setdefault("effects", [])
            index = next((i for i, effect in enumerate(effects) if effect.get("id") == effect_id), None)
            if index is None:
                raise EditValidationError(f"unknown effect: {effect_id}")
            effect = effects.pop(index)
            return self._record(op_type, sid, effect_id, {"track_id":tid,"clip_id":cid,"effect_id":effect_id}, {"track_id":tid,"clip_id":cid,"effect":effect,"index":index}, f"sequences.{sid}.tracks.{tid}.clips.{cid}.effects.{effect_id}")

        if op_type == "ADD_AUDIO_FADE":
            fade = copy.deepcopy(payload.get("fade") or {})
            fade["id"] = _identifier(fade.get("id"), "fade id")
            fade["start_us"] = _int_us(fade.get("start_us"), "fade.start_us")
            fade["end_us"] = _int_us(fade.get("end_us"), "fade.end_us")
            if fade["end_us"] <= fade["start_us"]: raise EditValidationError("fade end must be after start")
            if any(x.get("id") == fade["id"] for x in clip["audio_fades"]): raise EditValidationError("fade already exists")
            clip["audio_fades"].append(fade)
            return self._record(op_type, sid, fade["id"], {"track_id":tid,"clip_id":cid,"fade":fade}, {"track_id":tid,"clip_id":cid,"fade_id":fade["id"]}, f"sequences.{sid}.tracks.{tid}.clips.{cid}.audio_fades.{fade['id']}", fade["start_us"], fade["end_us"])

        if op_type in {"ADD_CAPTION", "UPDATE_CAPTION", "REMOVE_CAPTION", "SET_CAPTION_STYLE"}:
            if track["type"] != TrackType.CAPTIONS.value:
                raise EditValidationError("caption operations require a CAPTIONS layer")
            if op_type == "ADD_CAPTION":
                cap = copy.deepcopy(payload.get("caption") or {})
                for f in ("id","start_us","end_us","text"):
                    if f not in cap: raise EditValidationError(f"caption missing {f}")
                cap["id"] = _identifier(cap["id"], "caption id")
                cap["start_us"] = _int_us(cap["start_us"], "caption.start_us"); cap["end_us"] = _int_us(cap["end_us"], "caption.end_us")
                if cap["end_us"] <= cap["start_us"]: raise EditValidationError("caption end must be after start")
                cap.setdefault("style", {})
                if cap["id"] in track["captions"]: raise EditValidationError("caption already exists")
                track["captions"][cap["id"]] = cap; track["caption_order"].append(cap["id"])
                return self._record(op_type, sid, cap["id"], {"track_id":tid,"caption":cap}, {"track_id":tid,"caption_id":cap["id"]}, f"sequences.{sid}.tracks.{tid}.captions.{cap['id']}", cap["start_us"], cap["end_us"])
            cap_id = str(payload.get("caption_id") or entity_id or "")
            if cap_id not in track["captions"]: raise EditValidationError(f"unknown caption: {cap_id}")
            old_cap = copy.deepcopy(track["captions"][cap_id])
            if op_type == "REMOVE_CAPTION":
                index = track["caption_order"].index(cap_id); del track["captions"][cap_id]; track["caption_order"].remove(cap_id)
                return self._record(op_type, sid, cap_id, {"track_id":tid,"caption_id":cap_id}, {"track_id":tid,"caption":old_cap,"index":index}, f"sequences.{sid}.tracks.{tid}.captions.{cap_id}")
            if op_type == "UPDATE_CAPTION":
                updates = copy.deepcopy(payload.get("updates") or {})
                if "start_us" in updates: updates["start_us"] = _int_us(updates["start_us"], "caption.start_us")
                if "end_us" in updates: updates["end_us"] = _int_us(updates["end_us"], "caption.end_us")
                new_cap = copy.deepcopy(old_cap); new_cap.update(updates)
                if new_cap["end_us"] <= new_cap["start_us"]: raise EditValidationError("caption end must be after start")
                track["captions"][cap_id] = new_cap
                return self._record(op_type, sid, cap_id, {"track_id":tid,"caption_id":cap_id,"updates":updates}, {"track_id":tid,"caption_id":cap_id,"updates":old_cap}, f"sequences.{sid}.tracks.{tid}.captions.{cap_id}", new_cap["start_us"], new_cap["end_us"])
            style = copy.deepcopy(payload.get("style") or {})
            track["captions"][cap_id]["style"] = style
            return self._record(op_type, sid, cap_id, {"track_id":tid,"caption_id":cap_id,"style":style}, {"track_id":tid,"caption_id":cap_id,"style":old_cap.get("style",{})}, f"sequences.{sid}.tracks.{tid}.captions.{cap_id}.style")

        if op_type in {"ADD_OVERLAY", "REMOVE_OVERLAY"}:
            if track["type"] != TrackType.OVERLAY.value: raise EditValidationError("overlay operations require OVERLAY layer")
            overlays = track["properties"].setdefault("overlays", {})
            if op_type == "ADD_OVERLAY":
                overlay = copy.deepcopy(payload.get("overlay") or {})
                oid = _identifier(overlay.get("id"), "overlay id")
                if oid in overlays: raise EditValidationError("overlay id is duplicate")
                overlays[oid] = overlay
                return self._record(op_type, sid, oid, {"track_id":tid,"overlay":overlay}, {"track_id":tid,"overlay_id":oid}, f"sequences.{sid}.tracks.{tid}.overlays.{oid}")
            oid = str(payload.get("overlay_id") or entity_id or "")
            if oid not in overlays: raise EditValidationError("unknown overlay")
            overlay = overlays.pop(oid)
            return self._record(op_type, sid, oid, {"track_id":tid,"overlay_id":oid}, {"track_id":tid,"overlay":overlay}, f"sequences.{sid}.tracks.{tid}.overlays.{oid}")

        raise EditValidationError(f"operation not implemented: {op_type}")

    @staticmethod
    def _record(op_type: str, sequence_id: str | None, entity_id: str | None, up: dict[str, Any], down: dict[str, Any], path: str | None, start_us: int | None = None, end_us: int | None = None) -> dict[str, Any]:
        return {
            "operation_type": op_type,
            "sequence_id": sequence_id,
            "entity_id": entity_id,
            # Freeze both directions at operation time. Later operations in the
            # same commit may mutate the live state objects; history records
            # must never change with them.
            "up_payload": copy.deepcopy(up),
            "down_payload": copy.deepcopy(down),
            "affected_path": path,
            "start_us": start_us,
            "end_us": end_us,
        }

    def replay(self, state: dict[str, Any], record: dict[str, Any]) -> None:
        op_type = record["operation_type"]
        up = copy.deepcopy(record["up_payload"])
        if op_type == "APPLY_STATE_PATCH":
            state.clear(); state.update(copy.deepcopy(up["state"])); return
        # Reconstruct a public-operation request from its canonical up payload.
        self.apply(
            state,
            {
                "type": op_type,
                "sequence_id": record.get("sequence_id"),
                "entity_id": record.get("entity_id"),
                "payload": up,
            },
            allow_internal=True,
            # Historical commits predate the universal-lane invariant and may
            # legitimately contain overlaps. Replay must reproduce their exact
            # persisted state first; the service migrates that state only after
            # its hash has been verified.
            validate_timeline_overlaps=False,
        )
