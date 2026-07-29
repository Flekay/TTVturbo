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
        raise EditValidationError(f"unknown track: {track_id}") from exc


def _clip(track: dict[str, Any], clip_id: str) -> dict[str, Any]:
    try:
        return track["clips"][clip_id]
    except KeyError as exc:
        raise EditValidationError(f"unknown clip: {clip_id}") from exc


def _normalize_track(track: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(track)
    for field in ("id", "type", "name"):
        if field not in result:
            raise EditValidationError(f"track missing {field}")
    result["id"] = _identifier(result["id"], "track id")
    if result["type"] not in {t.value for t in TrackType}:
        raise EditValidationError(f"unknown track type: {result['type']}")
    result.setdefault("clips", {})
    result.setdefault("clip_order", [])
    result.setdefault("captions", {})
    result.setdefault("caption_order", [])
    result.setdefault("properties", {})
    return result


def _normalize_clip(clip: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(clip)
    for field in ("id", "source_media_item_id", "source_start_us", "source_end_us", "timeline_start_us"):
        if field not in result:
            raise EditValidationError(f"clip missing {field}")
    result["id"] = _identifier(result["id"], "clip id")
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

    def apply(self, state: dict[str, Any], operation: dict[str, Any], *, allow_internal: bool = False) -> dict[str, Any]:
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
            old = {k: seq[k] for k in ("width", "height", "fps_numerator", "fps_denominator", "format_profile")}
            merged = dict(old); merged.update(payload)
            checked = validate_sequence({"id": sid, "name": seq["name"], **merged})
            new = {k: checked[k] for k in old}
            seq.update(new)
            return self._record(op_type, sid, sid, new, old, f"sequences.{sid}.format")

        if op_type == "ADD_TRACK":
            track = _normalize_track(payload.get("track") or payload)
            tid = track["id"]
            if tid in seq["tracks"]:
                raise EditValidationError(f"track already exists: {tid}")
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

        if op_type == "REORDER_TRACK":
            order = list(payload.get("order") or [])
            if set(order) != set(seq["tracks"].keys()) or len(order) != len(seq["tracks"]):
                raise EditValidationError("track order must contain every track exactly once")
            old = list(seq["track_order"]); seq["track_order"] = order
            return self._record(op_type, sid, None, {"order": order}, {"order": old}, f"sequences.{sid}.track_order")

        if op_type == "SET_LAYOUT":
            old = copy.deepcopy(seq.get("layout")); layout = copy.deepcopy(payload.get("layout"))
            seq["layout"] = layout
            return self._record(op_type, sid, sid, {"layout":layout}, {"layout":old}, f"sequences.{sid}.layout")

        tid = str(payload.get("track_id") or "")
        track = _track(seq, tid)

        if op_type == "ADD_CLIP":
            clip = _normalize_clip(payload.get("clip") or {})
            source_keys = {(str(src.get("media_item_id")), src.get("asset_id")) for src in state.get("sources", [])}
            clip_source = (str(clip.get("source_media_item_id")), clip.get("source_asset_id"))
            if clip_source not in source_keys and (clip_source[0], None) not in source_keys:
                raise EditValidationError("clip must reference an immutable project source")
            cid = clip["id"]
            if cid in track["clips"]:
                raise EditValidationError(f"clip already exists: {cid}")
            track["clips"][cid] = clip
            index = payload.get("index")
            if index is None: track["clip_order"].append(cid)
            else: track["clip_order"].insert(max(0, min(int(index), len(track["clip_order"]))), cid)
            return self._record(op_type, sid, cid, {"track_id": tid, "clip": clip, "index": track["clip_order"].index(cid)}, {"track_id": tid, "clip_id": cid}, f"sequences.{sid}.tracks.{tid}.clips.{cid}", clip["timeline_start_us"], clip["timeline_start_us"] + (clip["source_end_us"] - clip["source_start_us"]))

        if op_type == "REMOVE_CLIP":
            cid = str(payload.get("clip_id") or entity_id or "")
            clip = copy.deepcopy(_clip(track, cid)); index = track["clip_order"].index(cid)
            del track["clips"][cid]; track["clip_order"].remove(cid)
            return self._record(op_type, sid, cid, {"track_id": tid, "clip_id": cid}, {"track_id": tid, "clip": clip, "index": index}, f"sequences.{sid}.tracks.{tid}.clips.{cid}")

        cid = str(payload.get("clip_id") or entity_id or "")
        clip = _clip(track, cid)

        if op_type == "MOVE_CLIP":
            old = {"track_id": tid, "clip_id": cid, "timeline_start_us": clip["timeline_start_us"]}
            value = _int_us(payload.get("timeline_start_us"), "timeline_start_us")
            clip["timeline_start_us"] = value
            return self._record(op_type, sid, cid, {"track_id": tid, "clip_id": cid, "timeline_start_us": value}, old, f"sequences.{sid}.tracks.{tid}.clips.{cid}.timeline_start_us", value, value + (clip["source_end_us"] - clip["source_start_us"]))

        if op_type == "TRIM_CLIP":
            old = {"track_id": tid, "clip_id": cid, "source_start_us": clip["source_start_us"], "source_end_us": clip["source_end_us"]}
            start = _int_us(payload.get("source_start_us", clip["source_start_us"]), "source_start_us")
            end = _int_us(payload.get("source_end_us", clip["source_end_us"]), "source_end_us")
            if end <= start: raise EditValidationError("source_end_us must be greater than source_start_us")
            clip["source_start_us"] = start; clip["source_end_us"] = end
            up = {"track_id": tid, "clip_id": cid, "source_start_us": start, "source_end_us": end}
            return self._record(op_type, sid, cid, up, old, f"sequences.{sid}.tracks.{tid}.clips.{cid}.trim", clip["timeline_start_us"], clip["timeline_start_us"] + (end-start))

        if op_type == "SPLIT_CLIP":
            split = _int_us(payload.get("split_source_us"), "split_source_us")
            if not clip["source_start_us"] < split < clip["source_end_us"]:
                raise EditValidationError("split_source_us must be inside clip")
            left_id = _identifier(payload.get("left_clip_id"), "left clip id"); right_id = _identifier(payload.get("right_clip_id"), "right clip id")
            if not left_id or not right_id or left_id == right_id or left_id in track["clips"] or right_id in track["clips"]:
                raise EditValidationError("split requires two new unique clip ids")
            original = copy.deepcopy(clip); index = track["clip_order"].index(cid)
            left = copy.deepcopy(clip); left.update({"id": left_id, "source_end_us": split})
            right = copy.deepcopy(clip); right.update({"id": right_id, "source_start_us": split, "timeline_start_us": clip["timeline_start_us"] + (split-clip["source_start_us"])})
            del track["clips"][cid]; track["clips"][left_id] = left; track["clips"][right_id] = right
            track["clip_order"][index:index+1] = [left_id, right_id]
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
            clip[key] = copy.deepcopy(value)
            up = {"track_id": tid, "clip_id": cid, "value": value}
            down = {"track_id": tid, "clip_id": cid, "value": old_value}
            return self._record(op_type, sid, cid, up, down, f"sequences.{sid}.tracks.{tid}.clips.{cid}.{key}")

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
                raise EditValidationError("caption operations require a CAPTIONS track")
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
            if track["type"] != TrackType.OVERLAY.value: raise EditValidationError("overlay operations require OVERLAY track")
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
        self.apply(state, {
            "type": op_type,
            "sequence_id": record.get("sequence_id"),
            "entity_id": record.get("entity_id"),
            "payload": up,
        }, allow_internal=True)
