"""Tests fuer den TTVturbo Voice-Script-Pack-Validator.

Die Tests arbeiten ausschliesslich auf temporaeren Kopien der Original-JSON-Dateien,
sodass die echten Dateien unangetastet bleiben.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import validate_voice_scripts as vvs  # noqa: E402

RECORDING_SRC = ROOT / "config" / "voice_lab" / "scripts" / "de-DE" / "ttvturbo_voice_pack_v1.json"
HOLDOUT_SRC = ROOT / "config" / "voice_lab" / "tests" / "de-DE" / "ttvturbo_voice_holdout_v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def tmp_pack(tmp_path: Path) -> tuple[Path, Path, dict, dict]:
    """Kopiere Originaldateien in ein temporaeres Verzeichnis."""
    rec = _load(RECORDING_SRC)
    hold = _load(HOLDOUT_SRC)
    rec_path = tmp_path / "recording.json"
    hold_path = tmp_path / "holdout.json"
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    hold_path.write_text(json.dumps(hold, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec_path, hold_path, rec, hold


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_valid_original_files(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, _, _ = tmp_pack
    errors = vvs.validate_files(rec_path, hold_path)
    assert errors == []


def test_wrong_prompt_count(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["prompts"] = rec["prompts"][:87]
    rec["prompt_count"] = 87
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("erwartet 88" in e for e in errors)


def test_duplicate_id(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["prompts"][1]["id"] = rec["prompts"][0]["id"]
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("doppelte IDs" in e for e in errors)


def test_duplicate_text(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["prompts"][1]["text"] = rec["prompts"][0]["text"]
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("doppelte Texte" in e for e in errors)


def test_missing_order(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    # Luecke in der order-Reihenfolge erzeugen
    rec["prompts"][5]["order"] = 999
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("order-Werte" in e for e in errors)


def test_wrong_style_count(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    # Einen neutral-Eintrag auf conversational umwidmen, damit neutral nur 7x vorkommt
    for p in rec["prompts"]:
        if p["style"] == "neutral" and p["category"] == "style":
            p["style"] = "conversational"
            break
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("style 'neutral'" in e for e in errors)


def test_holdout_text_identical_to_recording(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, hold = tmp_pack
    hold = copy.deepcopy(hold)
    hold["prompts"][0]["text"] = rec["prompts"][0]["text"]
    _write(hold_path, hold)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("Holdout-Texte duerfen nicht" in e for e in errors)


def test_empty_text(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["prompts"][0]["text"] = ""
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("leeren Text" in e for e in errors)


def test_wrong_locale(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["locale"] = "en-US"
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("falsche locale" in e for e in errors)


def test_invalid_duration_bounds(tmp_pack: tuple[Path, Path, dict, dict]) -> None:
    rec_path, hold_path, rec, _ = tmp_pack
    rec = copy.deepcopy(rec)
    rec["prompts"][0]["recommended_duration_seconds"] = {"min": 20, "max": 5}
    _write(rec_path, rec)
    errors = vvs.validate_files(rec_path, hold_path)
    assert any("Dauergrenzen" in e for e in errors)
