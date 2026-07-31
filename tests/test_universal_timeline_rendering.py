from __future__ import annotations

from pathlib import Path

from ttvturbo.rendering.worker import _compile


def test_renderer_compiles_universal_media_text_and_modular_fades(tmp_path: Path) -> None:
    desc = {
        "ffmpeg_path": "ffmpeg",
        "settings": {"include_audio": True, "mode": "PREVIEW"},
        "source_files": {
            "video": {"path": "/media/video.mp4", "has_video": True, "has_audio": True, "file_type": "video"},
            "image": {"path": "/media/image.png", "has_video": True, "has_audio": False, "file_type": "image"},
            "audio": {"path": "/media/audio.wav", "has_video": False, "has_audio": True, "file_type": "audio"},
        },
        "projection": {
            "output_settings": {"width": 1920, "height": 1080, "fps_numerator": 30, "fps_denominator": 1},
            "track_order": ["universal"],
            "tracks": {
                "universal": {
                    "id": "universal",
                    "type": "UNIVERSAL",
                    "clip_order": ["video", "image", "text", "audio"],
                    "clips": {
                        "video": {
                            "id": "video", "kind": "VIDEO", "source_media_item_id": "video",
                            "source_start_us": 0, "source_end_us": 2_000_000, "timeline_start_us": 0,
                            "effects": [{"id": "vin", "type": "FADE", "anchor": "START", "duration_us": 500_000}],
                        },
                        "image": {
                            "id": "image", "kind": "IMAGE", "source_media_item_id": "image",
                            "source_start_us": 0, "source_end_us": 2_000_000, "timeline_start_us": 2_000_000,
                            "effects": [{"id": "iout", "type": "FADE", "anchor": "END", "duration_us": 500_000}],
                        },
                        "text": {
                            "id": "text", "kind": "TEXT", "source_media_item_id": "",
                            "source_start_us": 0, "source_end_us": 2_000_000, "timeline_start_us": 4_000_000,
                            "text": {"content": "Editable title", "font_size": 72},
                            "effects": [{"id": "tin", "type": "FADE", "anchor": "START", "duration_us": 250_000}],
                        },
                        "audio": {
                            "id": "audio", "kind": "AUDIO", "source_media_item_id": "audio",
                            "source_start_us": 0, "source_end_us": 2_000_000, "timeline_start_us": 6_000_000,
                            "effects": [{"id": "aout", "type": "FADE", "anchor": "END", "duration_us": 500_000}],
                        },
                    },
                },
            },
        },
    }

    command, output_path, duration = _compile(desc, tmp_path)
    graph = (tmp_path / "filter_complex.txt").read_text(encoding="utf-8")

    assert duration == 8.0
    assert output_path == tmp_path / "output.mp4"
    assert "-loop" in command
    assert "drawtext=" in graph
    assert "fade=t=in" in graph
    assert "fade=t=out" in graph
    assert "afade=t=out" in graph
    assert "amix=inputs=2" in graph
