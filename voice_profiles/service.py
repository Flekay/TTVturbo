"""Voice-profile service: validation, progress derivation, persistence.

This is the isolated backend core. It knows nothing about FastAPI, React or
the Qwen3-TTS runtime. It composes :class:`ScriptLibrary` (script texts) and
:class:`VoiceProfileStorage` (atomic persistence) and adds:

* profile lifecycle (create / rename / archive / restore / delete);
* reference lifecycle (attach / detach / accept-review);
* progress derivation from the script library + stored references;
* recording-filename safety and server-side SHA-256 computation;
* quality-object validation against the voice-clone analyzer contract.

A process-local :class:`threading.Lock` guards every read-modify-write
sequence. There is no global GPU / job architecture here.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Optional

from .library import ScriptLibrary
from .schemas import (
    DEFAULT_LOCALE,
    MAX_PROFILE_NAME_LEN,
    QUALITY_CLASS_TO_STATUS,
    Progress,
    QualityClass,
    Reference,
    ReferenceStatus,
    SCHEMA_VERSION,
    SUPPORTED_LOCALES,
    VoiceProfileConflictError,
    VoiceProfileError,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
    VoiceProfileValidationError,
    VoiceScriptNotFoundError,
)
from .storage import VoiceProfileStorage

logger = logging.getLogger("ttvturbo.voice_profiles.service")


# Required keys inside the ``voice_clone_reference`` sub-object of a quality
# result. The analyzer contract is owned by :mod:`voice_clone.quality`; the
# voice-profile core only validates the surface it consumes.
REQUIRED_QUALITY_REF_KEYS = ("quality", "eligible", "reasons", "warnings")
VALID_QUALITY_CLASSES = {q.value for q in QualityClass}


class VoiceProfileService:
    """High-level service for voice profiles."""

    def __init__(
        self,
        library: ScriptLibrary,
        storage: VoiceProfileStorage,
        recordings_dir: Path,
    ) -> None:
        self.library = library
        self.storage = storage
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now_iso() -> str:
        return (
            _dt.datetime.now(tz=_dt.timezone.utc)
            .astimezone()
            .replace(microsecond=0)
            .isoformat()
        )

    @staticmethod
    def _new_uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _validate_name(name: Any) -> str:
        if not isinstance(name, str):
            raise VoiceProfileValidationError("name must be a string")
        trimmed = name.strip()
        if not trimmed:
            raise VoiceProfileValidationError("name must not be empty")
        if len(trimmed) > MAX_PROFILE_NAME_LEN:
            raise VoiceProfileValidationError(
                f"name exceeds {MAX_PROFILE_NAME_LEN} characters"
            )
        # Reject names that consist purely of invisible characters (after the
        # leading/trailing strip already removed ASCII whitespace, still reject
        # zero-width / format / control codepoints).
        if not _has_visible_content(trimmed):
            raise VoiceProfileValidationError("name must contain visible characters")
        return trimmed

    @staticmethod
    def _validate_locale(locale: Any) -> str:
        if not isinstance(locale, str) or locale not in SUPPORTED_LOCALES:
            raise VoiceProfileValidationError(
                f"unsupported locale: {locale!r} (supported: {sorted(SUPPORTED_LOCALES)})"
            )
        return locale

    def _resolve_recording(self, recording_filename: str) -> Path:
        """Resolve a recording filename to an absolute path inside recordings_dir.

        Blocks path traversal, absolute paths, hidden/temp files, non-WAV.
        """
        if not recording_filename or not isinstance(recording_filename, str):
            raise VoiceProfileValidationError("recording_filename is empty")
        if "/" in recording_filename or "\\" in recording_filename:
            raise VoiceProfileValidationError(
                "recording_filename must be a plain filename"
            )
        if recording_filename.startswith(".") or recording_filename.startswith("~"):
            raise VoiceProfileValidationError(
                "recording_filename must not be a hidden file"
            )
        if ".." in recording_filename:
            raise VoiceProfileValidationError(
                "recording_filename must not contain parent references"
            )
        safe = Path(recording_filename).name
        if safe != recording_filename:
            raise VoiceProfileValidationError(
                "recording_filename must be a plain filename"
            )
        if not safe.lower().endswith(".wav"):
            raise VoiceProfileValidationError("recording_filename must be a .wav file")
        resolved = (self.recordings_dir / safe).resolve()
        try:
            resolved.relative_to(self.recordings_dir.resolve())
        except ValueError as exc:
            raise VoiceProfileValidationError(
                "recording_filename escapes the recordings directory"
            ) from exc
        if not resolved.is_file():
            raise VoiceProfileValidationError(
                f"recording does not exist: {safe}"
            )
        return resolved

    @staticmethod
    def _validate_quality(quality: Any) -> tuple[QualityClass, ReferenceStatus]:
        """Validate the analyzer result object and return (class, status)."""
        if not isinstance(quality, dict):
            raise VoiceProfileValidationError("quality must be a dict")
        vcr = quality.get("voice_clone_reference")
        if not isinstance(vcr, dict):
            raise VoiceProfileValidationError(
                "quality.voice_clone_reference is required and must be an object"
            )
        for key in REQUIRED_QUALITY_REF_KEYS:
            if key not in vcr:
                raise VoiceProfileValidationError(
                    f"quality.voice_clone_reference.{key} is required"
                )
        q_class_raw = vcr.get("quality")
        if not isinstance(q_class_raw, str) or q_class_raw not in VALID_QUALITY_CLASSES:
            raise VoiceProfileValidationError(
                f"quality.voice_clone_reference.quality must be one of "
                f"{sorted(VALID_QUALITY_CLASSES)}"
            )
        if not isinstance(vcr.get("eligible"), bool):
            raise VoiceProfileValidationError(
                "quality.voice_clone_reference.eligible must be a bool"
            )
        if not isinstance(vcr.get("reasons"), list):
            raise VoiceProfileValidationError(
                "quality.voice_clone_reference.reasons must be a list"
            )
        if not isinstance(vcr.get("warnings"), list):
            raise VoiceProfileValidationError(
                "quality.voice_clone_reference.warnings must be a list"
            )
        q_class = QualityClass(q_class_raw)
        status = QUALITY_CLASS_TO_STATUS[q_class]
        return q_class, status

    # ------------------------------------------------------------------ progress
    def _compute_progress(self, profile_dict: dict) -> dict:
        """Derive progress from the script library + stored references."""
        pack_prompts = self.library.get_recording_prompts()
        total = len(pack_prompts)
        pack_ids = {p.id for p in pack_prompts}

        refs: dict[str, Any] = profile_dict.get("references", {}) or {}
        # Only references whose script id is in the recording pack count
        # toward progress. Holdout ids are ignored.
        pack_refs = [r for r in refs.values() if r.get("script_id") in pack_ids]

        recorded = len(pack_refs)
        accepted = sum(1 for r in pack_refs if r.get("status") == ReferenceStatus.ACCEPTED.value)
        review = sum(1 for r in pack_refs if r.get("status") == ReferenceStatus.REVIEW.value)
        rejected = sum(1 for r in pack_refs if r.get("status") == ReferenceStatus.REJECTED.value)
        missing = total - recorded

        percentage = round((accepted / total) * 100.0, 1) if total else 0.0
        clone_ready = accepted >= 1
        pack_complete = total > 0 and accepted == total

        return Progress(
            total=total,
            missing=missing,
            recorded=recorded,
            accepted=accepted,
            review=review,
            rejected=rejected,
            percentage=percentage,
            clone_ready=clone_ready,
            pack_complete=pack_complete,
        ).model_dump()

    def _with_progress(self, profile_dict: dict) -> dict:
        out = dict(profile_dict)
        out["progress"] = self._compute_progress(profile_dict)
        return out

    # ------------------------------------------------------------------ profile lifecycle
    def create_profile(self, name: str, locale: str = DEFAULT_LOCALE) -> dict:
        clean_name = self._validate_name(name)
        clean_locale = self._validate_locale(locale)
        with self._lock:
            profile_id = self._new_uuid()
            now = self._now_iso()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "id": profile_id,
                "name": clean_name,
                "locale": clean_locale,
                "created_at": now,
                "updated_at": now,
                "archived": False,
                "references": {},
            }
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def list_profiles(self, include_archived: bool = False) -> list[dict]:
        out: list[dict] = []
        for payload in self.storage.iter_profiles():
            if payload.get("archived") and not include_archived:
                continue
            out.append(self._with_progress(payload))
        out.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return out

    def get_profile(self, profile_id: str) -> dict:
        with self._lock:
            payload = self.storage.load_profile(profile_id)
        return self._with_progress(payload)

    def rename_profile(self, profile_id: str, name: str) -> dict:
        clean_name = self._validate_name(name)
        with self._lock:
            payload = self.storage.load_profile(profile_id)
            payload["name"] = clean_name
            payload["updated_at"] = self._now_iso()
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def archive_profile(self, profile_id: str) -> dict:
        with self._lock:
            payload = self.storage.load_profile(profile_id)
            payload["archived"] = True
            payload["updated_at"] = self._now_iso()
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def restore_profile(self, profile_id: str) -> dict:
        with self._lock:
            payload = self.storage.load_profile(profile_id)
            payload["archived"] = False
            payload["updated_at"] = self._now_iso()
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            # load_profile raises NotFound if the profile does not exist; that
            # is the contract we want here too.
            self.storage.load_profile(profile_id)
            return self.storage.delete_profile(profile_id)

    # ------------------------------------------------------------------ references
    def attach_reference(
        self,
        profile_id: str,
        script_id: str,
        recording_filename: str,
        quality: dict,
    ) -> dict:
        """Attach (or replace) a reference for ``script_id`` on a profile.

        The service:

        * resolves the script text/style/category from the library;
        * validates and resolves the recording filename;
        * computes SHA-256 itself from the file on disk;
        * validates the quality object and derives the canonical status.
        """
        if not isinstance(script_id, str) or not script_id:
            raise VoiceProfileValidationError("script_id is required")
        prompt = self.library.get_prompt(script_id)  # raises VoiceScriptNotFoundError

        # Holdout scripts may not be attached as pack references. They are
        # reserved for later evaluation.
        if self.library.is_holdout_id(script_id):
            raise VoiceProfileValidationError(
                f"script id {script_id!r} is a holdout prompt and cannot be attached"
            )

        resolved = self._resolve_recording(recording_filename)
        sha256 = self._file_sha256(resolved)
        q_class, status = self._validate_quality(quality)

        with self._lock:
            payload = self.storage.load_profile(profile_id)
            refs: dict = payload.setdefault("references", {})
            now = self._now_iso()
            existing = refs.get(script_id)
            attached_at = existing.get("attached_at") if existing else now

            ref = Reference(
                script_id=prompt.id,
                script_text=prompt.text,
                category=prompt.category,
                style=prompt.style,
                recording_filename=recording_filename,
                recording_sha256=sha256,
                quality=quality,
                quality_class=q_class,
                status=status,
                review_accepted=(status == ReferenceStatus.ACCEPTED and existing is not None
                                 and existing.get("review_accepted") is True),
                attached_at=attached_at,
                updated_at=now,
            )
            refs[script_id] = ref.model_dump()
            payload["updated_at"] = now
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def detach_reference(self, profile_id: str, script_id: str) -> dict:
        if not isinstance(script_id, str) or not script_id:
            raise VoiceProfileValidationError("script_id is required")
        with self._lock:
            payload = self.storage.load_profile(profile_id)
            refs: dict = payload.get("references", {}) or {}
            if script_id not in refs:
                raise VoiceScriptNotFoundError(
                    f"profile {profile_id} has no reference for {script_id}"
                )
            refs.pop(script_id)
            payload["references"] = refs
            payload["updated_at"] = self._now_iso()
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    def accept_review_reference(self, profile_id: str, script_id: str) -> dict:
        """Explicitly confirm a REVIEW reference. REJECTED references cannot be accepted."""
        if not isinstance(script_id, str) or not script_id:
            raise VoiceProfileValidationError("script_id is required")
        with self._lock:
            payload = self.storage.load_profile(profile_id)
            refs: dict = payload.get("references", {}) or {}
            if script_id not in refs:
                raise VoiceScriptNotFoundError(
                    f"profile {profile_id} has no reference for {script_id}"
                )
            ref = refs[script_id]
            current_status = ref.get("status")
            if current_status == ReferenceStatus.REJECTED.value:
                raise VoiceProfileConflictError(
                    f"cannot accept a REJECTED reference for {script_id}"
                )
            if current_status != ReferenceStatus.REVIEW.value:
                # Already ACCEPTED (or somehow unknown): nothing to do, but
                # treat unknown as a conflict.
                if current_status == ReferenceStatus.ACCEPTED.value:
                    ref["review_accepted"] = True
                    ref["updated_at"] = self._now_iso()
                    refs[script_id] = ref
                    payload["references"] = refs
                    payload["updated_at"] = ref["updated_at"]
                    self.storage.save_profile(payload)
                    return self._with_progress(payload)
                raise VoiceProfileConflictError(
                    f"reference for {script_id} is in unexpected status {current_status!r}"
                )
            ref["status"] = ReferenceStatus.ACCEPTED.value
            ref["review_accepted"] = True
            ref["updated_at"] = self._now_iso()
            refs[script_id] = ref
            payload["references"] = refs
            payload["updated_at"] = ref["updated_at"]
            self.storage.save_profile(payload)
            return self._with_progress(payload)

    # ------------------------------------------------------------------ queries
    def find_profiles_using_recording(self, recording_filename: str) -> list[dict]:
        """Return all profiles that have a reference pointing at the given WAV."""
        if not isinstance(recording_filename, str) or not recording_filename:
            raise VoiceProfileValidationError("recording_filename is required")
        out: list[dict] = []
        for payload in self.storage.iter_profiles():
            refs: dict = payload.get("references", {}) or {}
            for ref in refs.values():
                if ref.get("recording_filename") == recording_filename:
                    out.append(self._with_progress(payload))
                    break
        out.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return out

    def list_accepted_references(self, profile_id: str) -> list[dict]:
        with self._lock:
            payload = self.storage.load_profile(profile_id)
        refs: dict = payload.get("references", {}) or {}
        accepted = [
            r for r in refs.values() if r.get("status") == ReferenceStatus.ACCEPTED.value
        ]
        accepted.sort(key=lambda r: r.get("attached_at", ""))
        return accepted


# ---------------------------------------------------------------------------
# module-private helpers
# ---------------------------------------------------------------------------

def _has_visible_content(text: str) -> bool:
    """True if ``text`` contains at least one visible (non-invisible) character."""
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        # Cf = format, Cc = control, Cs = surrogate, Mn = nonspacing mark,
        # Me = enclosing mark. All of these are "invisible" for naming purposes.
        if cat in ("Cf", "Cc", "Cs", "Mn", "Me"):
            continue
        return True
    return False
