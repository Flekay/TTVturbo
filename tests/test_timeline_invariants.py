from __future__ import annotations

import pytest

from ttvturbo.editing import EditValidationError, OperationEngine
from ttvturbo.editing.operations import empty_state


def _timeline() -> tuple[OperationEngine, dict, str, str]:
    engine = OperationEngine()
    state = empty_state("project", [{
        "id": "source-1",
        "media_item_id": "media-1",
        "sha256": "a" * 64,
        "created_at": "2026-07-30T00:00:00Z",
    }])
    sequence_id = "sequence-1"
    track_id = "track-1"
    engine.apply(state, {
        "type": "CREATE_SEQUENCE",
        "payload": {
            "sequence": {
                "id": sequence_id,
                "name": "Main",
                "width": 1920,
                "height": 1080,
                "fps_numerator": 30,
                "fps_denominator": 1,
                "format_profile": "DESKTOP_16_9",
            },
        },
    })
    engine.apply(state, {
        "type": "ADD_TRACK",
        "sequence_id": sequence_id,
        "payload": {"track": {"id": track_id, "type": "UNIVERSAL", "name": "Spur 1"}},
    })
    return engine, state, sequence_id, track_id


def _media_clip(clip_id: str, kind: str, start_us: int, duration_us: int = 2_000_000) -> dict:
    return {
        "id": clip_id,
        "kind": kind,
        "source_media_item_id": "media-1",
        "source_start_us": 0,
        "source_end_us": duration_us,
        "timeline_start_us": start_us,
    }


def _add(engine: OperationEngine, state: dict, sequence_id: str, track_id: str, clip: dict) -> None:
    engine.apply(state, {
        "type": "ADD_CLIP",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip": clip},
    })


def test_universal_track_accepts_every_element_kind_and_derives_occupancy() -> None:
    engine, state, sequence_id, track_id = _timeline()
    _add(engine, state, sequence_id, track_id, _media_clip("video", "VIDEO", 0))
    _add(engine, state, sequence_id, track_id, _media_clip("audio", "AUDIO", 2_000_000))
    _add(engine, state, sequence_id, track_id, _media_clip("image", "IMAGE", 4_000_000))
    _add(engine, state, sequence_id, track_id, {
        "id": "text",
        "kind": "TEXT",
        "source_media_item_id": "",
        "source_start_us": 0,
        "source_end_us": 2_000_000,
        "timeline_start_us": 6_000_000,
        "text": {"content": "Editable text"},
    })

    track = state["sequences"][sequence_id]["tracks"][track_id]
    assert [track["clips"][clip_id]["kind"] for clip_id in track["clip_order"]] == [
        "VIDEO", "AUDIO", "IMAGE", "TEXT",
    ]
    # Occupancy is intentionally derived instead of persisted so historical
    # project-state hashes remain stable. Adjacent half-open intervals are valid.
    assert "occupied_ranges" not in track
    assert [
        (clip_id, track["clips"][clip_id]["timeline_start_us"], track["clips"][clip_id]["source_end_us"])
        for clip_id in track["clip_order"]
    ] == [
        ("video", 0, 2_000_000),
        ("audio", 2_000_000, 2_000_000),
        ("image", 4_000_000, 2_000_000),
        ("text", 6_000_000, 2_000_000),
    ]


def test_overlap_is_rejected_for_add_move_trim_and_speed_changes() -> None:
    engine, state, sequence_id, track_id = _timeline()
    _add(engine, state, sequence_id, track_id, _media_clip("left", "VIDEO", 0))
    _add(engine, state, sequence_id, track_id, _media_clip("right", "AUDIO", 3_000_000))

    with pytest.raises(EditValidationError, match="timeline overlap"):
        _add(engine, state, sequence_id, track_id, _media_clip("inside", "IMAGE", 1_000_000))

    with pytest.raises(EditValidationError, match="timeline overlap"):
        engine.apply(state, {
            "type": "MOVE_CLIP",
            "sequence_id": sequence_id,
            "payload": {"track_id": track_id, "clip_id": "left", "timeline_start_us": 2_000_000},
        })

    with pytest.raises(EditValidationError, match="timeline overlap"):
        engine.apply(state, {
            "type": "TRIM_CLIP",
            "sequence_id": sequence_id,
            "payload": {"track_id": track_id, "clip_id": "left", "source_end_us": 4_000_000},
        })

    with pytest.raises(EditValidationError, match="timeline overlap"):
        engine.apply(state, {
            "type": "SET_SPEED",
            "sequence_id": sequence_id,
            "payload": {"track_id": track_id, "clip_id": "left", "value": 0.5},
        })

    track = state["sequences"][sequence_id]["tracks"][track_id]
    assert track["clips"]["left"]["timeline_start_us"] == 0
    assert track["clips"]["left"]["source_end_us"] == 2_000_000
    assert track["clips"]["left"]["speed"] == 1.0


def test_modular_fades_attach_to_any_element_and_text_remains_editable() -> None:
    engine, state, sequence_id, track_id = _timeline()
    _add(engine, state, sequence_id, track_id, {
        "id": "title",
        "kind": "TEXT",
        "source_media_item_id": "",
        "source_start_us": 0,
        "source_end_us": 5_000_000,
        "timeline_start_us": 0,
        "text": {"content": "Before"},
    })
    effect = {
        "id": "fade-in",
        "type": "FADE",
        "anchor": "START",
        "duration_us": 1_000_000,
        "enabled": True,
        "parameters": {},
    }
    engine.apply(state, {
        "type": "ADD_EFFECT",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "title", "effect": effect},
    })
    engine.apply(state, {
        "type": "SET_TEXT",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "title", "value": {"content": "After", "font_size": 80}},
    })

    clip = state["sequences"][sequence_id]["tracks"][track_id]["clips"]["title"]
    assert clip["text"]["content"] == "After"
    assert clip["effects"] == [effect]

    engine.apply(state, {
        "type": "UPDATE_EFFECT",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "title", "effect_id": "fade-in", "updates": {"duration_us": 1_500_000}},
    })
    assert clip["effects"][0]["duration_us"] == 1_500_000

    with pytest.raises(EditValidationError, match="already has a fade"):
        engine.apply(state, {
            "type": "ADD_EFFECT",
            "sequence_id": sequence_id,
            "payload": {"track_id": track_id, "clip_id": "title", "effect": {**effect, "id": "another-fade"}},
        })

    engine.apply(state, {
        "type": "REMOVE_EFFECT",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "title", "effect_id": "fade-in"},
    })
    assert clip["effects"] == []


def test_legacy_audio_track_keeps_historical_clip_shape() -> None:
    engine, state, sequence_id, _ = _timeline()
    legacy_clip = _media_clip("legacy", "AUDIO", 0)
    legacy_clip.pop("kind")
    engine.apply(state, {
        "type": "ADD_TRACK",
        "sequence_id": sequence_id,
        "payload": {
            "track": {
                "id": "legacy-audio",
                "type": "AUDIO",
                "name": "Legacy audio",
                "clips": {"legacy": legacy_clip},
                "clip_order": ["legacy"],
            },
        },
    })
    stored = state["sequences"][sequence_id]["tracks"]["legacy-audio"]["clips"]["legacy"]
    assert "kind" not in stored
    assert "effects" not in stored


def test_next_available_add_is_resolved_authoritatively() -> None:
    engine, state, sequence_id, track_id = _timeline()
    _add(engine, state, sequence_id, track_id, _media_clip("existing", "VIDEO", 0, 5_000_000))

    engine.apply(state, {
        "type": "ADD_CLIP",
        "sequence_id": sequence_id,
        "payload": {
            "track_id": track_id,
            "placement": "NEXT_AVAILABLE",
            "clip": _media_clip("new", "IMAGE", 0, 14_001_000),
        },
    })

    track = state["sequences"][sequence_id]["tracks"][track_id]
    assert track["clips"]["new"]["timeline_start_us"] == 5_000_000


def test_layers_can_be_renamed_and_reordered() -> None:
    engine, state, sequence_id, track_id = _timeline()
    engine.apply(state, {
        "type": "ADD_TRACK",
        "sequence_id": sequence_id,
        "payload": {"track": {"id": "track-2", "type": "UNIVERSAL", "name": "Layer 2"}},
    })

    engine.apply(state, {
        "type": "RENAME_TRACK",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "name": "Gameplay"},
    })
    engine.apply(state, {
        "type": "REORDER_TRACK",
        "sequence_id": sequence_id,
        "payload": {"order": ["track-2", track_id]},
    })

    sequence = state["sequences"][sequence_id]
    assert sequence["tracks"][track_id]["name"] == "Gameplay"
    assert sequence["track_order"] == ["track-2", track_id]

    with pytest.raises(EditValidationError, match="must not be empty"):
        engine.apply(state, {
            "type": "RENAME_TRACK",
            "sequence_id": sequence_id,
            "payload": {"track_id": track_id, "name": "   "},
        })


def test_left_trim_can_be_extended_again_without_temporary_overlap() -> None:
    engine, state, sequence_id, track_id = _timeline()
    _add(engine, state, sequence_id, track_id, {
        **_media_clip("trimmed", "VIDEO", 1_000_000, 5_000_000),
        "source_start_us": 1_000_000,
    })
    _add(engine, state, sequence_id, track_id, _media_clip("next", "IMAGE", 5_000_000, 2_000_000))

    # The UI intentionally performs these two operations in this order when
    # the left edge is extended: move the shorter clip first, then restore its
    # source range. The final right edge remains at 5 seconds.
    engine.apply(state, {
        "type": "MOVE_CLIP",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "trimmed", "timeline_start_us": 0},
    })
    engine.apply(state, {
        "type": "TRIM_CLIP",
        "sequence_id": sequence_id,
        "payload": {"track_id": track_id, "clip_id": "trimmed", "source_start_us": 0, "source_end_us": 5_000_000},
    })

    clip = state["sequences"][sequence_id]["tracks"][track_id]["clips"]["trimmed"]
    assert clip["timeline_start_us"] == 0
    assert clip["source_start_us"] == 0
    assert clip["source_end_us"] == 5_000_000
