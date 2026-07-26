#!/usr/bin/env python3
"""Validator for the TTVturbo German voice script pack.

Prueft die Aufnahme-JSON-Datei auf Struktur, Eindeutigkeit
und Plausibilitaet. Siehe README/Spezifikation fuer die genaue Pruefliste.

Exitcodes:
    0 = gueltig
    1 = Validierungsfehler
    2 = technischer Fehler
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECORDING_PATH = ROOT / "config" / "voice_lab" / "scripts" / "de-DE" / "ttvturbo_voice_pack_v1.json"

SUPPORTED_SCHEMA_VERSIONS = {1}
EXPECTED_LOCALE = "de-DE"
EXPECTED_RECORDING_COUNT = 88

STYLE_CATEGORIES = [
    "neutral",
    "conversational",
    "amused",
    "dry_humor",
    "energetic",
    "serious",
    "skeptical",
    "hook_question",
]
EXTRA_CATEGORIES = ["numbers_dates", "technology_gaming", "phonetic_coverage"]
EXPECTED_PER_CATEGORY = 8

TERMINAL_PUNCTUATION = ".!?:"
VALID_CATEGORIES = {"style", *EXTRA_CATEGORIES}


class ValidationFailure(Exception):
    """Ein einzelner Validierungsfehler."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationFailure(f"Datei konnte nicht gelesen werden: {path} ({exc})")
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValidationFailure(f"Datei ist kein gueltiges UTF-8: {path} ({exc})")
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"Datei ist kein gueltiges JSON: {path} ({exc})")


def _check_common_structure(data: dict[str, Any], path: Path, expected_kind: str) -> None:
    if not isinstance(data, dict):
        raise ValidationFailure(f"{path}: Wurzel muss ein JSON-Objekt sein.")
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationFailure(
            f"{path}: nicht unterstuetzte schema_version {schema_version!r}"
        )
    if data.get("locale") != EXPECTED_LOCALE:
        raise ValidationFailure(
            f"{path}: falsche locale {data.get('locale')!r}, erwartet {EXPECTED_LOCALE!r}"
        )
    if data.get("kind") != expected_kind:
        raise ValidationFailure(
            f"{path}: falsches kind {data.get('kind')!r}, erwartet {expected_kind!r}"
        )
    prompts = data.get("prompts")
    if not isinstance(prompts, list):
        raise ValidationFailure(f"{path}: 'prompts' muss eine Liste sein.")
    declared = data.get("prompt_count")
    if not isinstance(declared, int) or declared != len(prompts):
        raise ValidationFailure(
            f"{path}: prompt_count {declared!r} stimmt nicht mit Anzahl prompts ({len(prompts)})."
        )


def _check_prompt(prompt: dict[str, Any], path: Path, index: int) -> None:
    if not isinstance(prompt, dict):
        raise ValidationFailure(f"{path}: Prompt {index} ist kein Objekt.")
    for key in ("id", "order", "category", "style", "text", "recommended_duration_seconds"):
        if key not in prompt:
            raise ValidationFailure(f"{path}: Prompt {index} fehlt Feld {key!r}.")

    pid = prompt.get("id")
    if not isinstance(pid, str) or not pid:
        raise ValidationFailure(f"{path}: Prompt {index} hat ungueltige id.")

    order = prompt.get("order")
    if not isinstance(order, int) or order < 1:
        raise ValidationFailure(f"{path}: Prompt {index} hat ungueltige order {order!r}.")

    text = prompt.get("text")
    if not isinstance(text, str) or not text:
        raise ValidationFailure(f"{path}: Prompt {pid} hat leeren Text.")
    if text != text.strip():
        raise ValidationFailure(
            f"{path}: Prompt {pid} hat fuehrende oder abschliessende Leerzeichen."
        )
    if not text[-1] in TERMINAL_PUNCTUATION:
        raise ValidationFailure(
            f"{path}: Prompt {pid} endet nicht mit sinnvoller Zeichensetzung."
        )

    dur = prompt.get("recommended_duration_seconds")
    if not isinstance(dur, dict):
        raise ValidationFailure(f"{path}: Prompt {pid} hat ungueltige Dauergrenzen.")
    mn = dur.get("min")
    mx = dur.get("max")
    if not isinstance(mn, int) or not isinstance(mx, int):
        raise ValidationFailure(f"{path}: Prompt {pid} hat nicht-int Dauergrenzen.")
    if mn < 0 or mx < 0 or mn > mx:
        raise ValidationFailure(
            f"{path}: Prompt {pid} hat ungueltige Dauergrenzen (min={mn}, max={mx})."
        )


def _check_ids_and_orders(prompts: list[dict[str, Any]], path: Path) -> None:
    ids = [p["id"] for p in prompts]
    dup_ids = [pid for pid, count in Counter(ids).items() if count > 1]
    if dup_ids:
        raise ValidationFailure(f"{path}: doppelte IDs: {sorted(set(dup_ids))}.")
    orders = [p["order"] for p in prompts]
    dup_orders = [o for o, count in Counter(orders).items() if count > 1]
    if dup_orders:
        raise ValidationFailure(f"{path}: doppelte order-Werte: {sorted(set(dup_orders))}.")
    expected_orders = list(range(1, len(prompts) + 1))
    if sorted(orders) != expected_orders:
        raise ValidationFailure(
            f"{path}: order-Werte sind nicht lueckenlos ab 1 (got {sorted(orders)})."
        )


def _check_unique_texts(prompts: list[dict[str, Any]], path: Path) -> None:
    texts = [p["text"] for p in prompts]
    dup = [t for t, count in Counter(texts).items() if count > 1]
    if dup:
        raise ValidationFailure(f"{path}: doppelte Texte gefunden: {dup[:3]} ...")


def validate_recording(data: dict[str, Any], path: Path) -> None:
    _check_common_structure(data, path, "recording_pack")
    prompts = data["prompts"]
    if len(prompts) != EXPECTED_RECORDING_COUNT:
        raise ValidationFailure(
            f"{path}: erwartet {EXPECTED_RECORDING_COUNT} Aufnahmeskripte, got {len(prompts)}."
        )

    style_counts: Counter[str] = Counter()
    extra_counts: Counter[str] = Counter()
    for idx, prompt in enumerate(prompts):
        _check_prompt(prompt, path, idx)
        category = prompt.get("category")
        if category == "style":
            style = prompt.get("style")
            if style not in STYLE_CATEGORIES:
                raise ValidationFailure(
                    f"{path}: Prompt {prompt.get('id')} hat unbekannten style {style!r}."
                )
            style_counts[style] += 1
        elif category in EXTRA_CATEGORIES:
            extra_counts[category] += 1
        else:
            raise ValidationFailure(
                f"{path}: Prompt {prompt.get('id')} hat unbekannte category {category!r}."
            )

    for style in STYLE_CATEGORIES:
        if style_counts[style] != EXPECTED_PER_CATEGORY:
            raise ValidationFailure(
                f"{path}: style {style!r} hat {style_counts[style]} Skripte, "
                f"erwartet {EXPECTED_PER_CATEGORY}."
            )
    for cat in EXTRA_CATEGORIES:
        if extra_counts[cat] != EXPECTED_PER_CATEGORY:
            raise ValidationFailure(
                f"{path}: Kategorie {cat!r} hat {extra_counts[cat]} Skripte, "
                f"erwartet {EXPECTED_PER_CATEGORY}."
            )

    _check_ids_and_orders(prompts, path)
    _check_unique_texts(prompts, path)


def validate_files(recording_path: Path = RECORDING_PATH) -> list[str]:
    errors: list[str] = []

    if not recording_path.is_file():
        errors.append(f"Datei fehlt: {recording_path}")

    if errors:
        return errors

    recording: dict[str, Any] | None = None

    try:
        recording = _load_json(recording_path)
    except ValidationFailure as exc:
        errors.append(str(exc))

    if recording is not None:
        try:
            validate_recording(recording, recording_path)
        except ValidationFailure as exc:
            errors.append(str(exc))

    return errors


def main() -> int:
    try:
        errors = validate_files()
    except Exception as exc:  # technischer Fehler
        print(f"Technischer Fehler: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("Validierung fehlgeschlagen:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"Validierung erfolgreich: {EXPECTED_RECORDING_COUNT} Aufnahmeskripte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
