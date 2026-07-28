"""Script library loader for the voice-profile backend core.

Loads the recording-pack JSON file that lives under
``config/voice_lab/...``. The file path is injected through the
constructor so unit tests can point at temporary fixtures.

The loader is strict:

* the top-level ``schema_version`` must be a supported value;
* prompt ids must be unique within the file;
* if ``expected_prompt_count`` is present, the prompt list length must match;
* every prompt must match :class:`ScriptPrompt`.

No prompt text is duplicated in Python: the loader only returns what is
declared in the JSON file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .schemas import (
    SUPPORTED_SCHEMA_VERSIONS,
    ScriptPack,
    ScriptPrompt,
    VoiceProfileError,
    VoiceProfileStorageError,
    VoiceScriptNotFoundError,
)

logger = logging.getLogger("ttvturbo.voice_profiles.library")


# Default production path. It is intentionally *not* imported at module
# import time; the constructor resolves it lazily so tests that never touch
# the real config tree can run without it.
DEFAULT_PACK_PATH = Path("config/voice_lab/scripts/de-DE/ttvturbo_voice_pack_v1.json")


class ScriptLibrary:
    """In-memory loader for the recording pack."""

    def __init__(self, pack_path: Path = DEFAULT_PACK_PATH) -> None:
        self.pack_path = Path(pack_path)
        self._pack: Optional[ScriptPack] = None
        self._pack_by_id: dict[str, ScriptPrompt] = {}

    # ------------------------------------------------------------------ loading
    def _load_file(self, path: Path) -> ScriptPack:
        if not path.is_file():
            raise VoiceProfileStorageError(f"script file not found: {path}")
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceProfileStorageError(f"could not read script file {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise VoiceProfileStorageError(f"script file {path} must be a JSON object")

        schema_version = raw.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise VoiceProfileStorageError(
                f"unsupported schema_version {schema_version!r} in {path}"
            )

        try:
            pack = ScriptPack.model_validate(raw)
        except ValidationError as exc:
            raise VoiceProfileStorageError(f"invalid script file {path}: {exc}") from exc

        # Duplicate id check.
        seen: set[str] = set()
        for prompt in pack.prompts:
            if prompt.id in seen:
                raise VoiceProfileStorageError(
                    f"duplicate prompt id {prompt.id!r} in {path}"
                )
            seen.add(prompt.id)

        # Prompt count check: the real files declare ``prompt_count`` and it
        # must match the actual number of prompts.
        if len(pack.prompts) != pack.prompt_count:
            raise VoiceProfileStorageError(
                f"prompt count mismatch in {path}: "
                f"declared {pack.prompt_count}, got {len(pack.prompts)}"
            )

        return pack

    def _ensure_loaded(self) -> None:
        if self._pack is None:
            pack = self._load_file(self.pack_path)
            self._pack = pack
            self._pack_by_id = {p.id: p for p in pack.prompts}

    # ------------------------------------------------------------------ public API
    def get_recording_prompts(self) -> list[ScriptPrompt]:
        """Return the recording-pack prompts in declaration order."""
        self._ensure_loaded()
        assert self._pack is not None
        return list(self._pack.prompts)

    def get_prompt(self, prompt_id: str) -> ScriptPrompt:
        """Return a single prompt by id."""
        self._ensure_loaded()
        if prompt_id in self._pack_by_id:
            return self._pack_by_id[prompt_id]
        raise VoiceScriptNotFoundError(f"unknown script id: {prompt_id}")

    def get_pack_metadata(self) -> dict:
        """Return the pack metadata (everything except the prompt list)."""
        self._ensure_loaded()
        assert self._pack is not None
        data = self._pack.model_dump()
        data.pop("prompts", None)
        return data

    def is_pack_id(self, prompt_id: str) -> bool:
        """True if the id belongs to the recording pack."""
        self._ensure_loaded()
        return prompt_id in self._pack_by_id
