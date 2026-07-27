"""Tests for Twitch profile persistence, sync, import and the VOD API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vod_pipeline import (
    TwitchClientError,
    TwitchProfileConflictError,
    TwitchProfileNotFoundError,
    TwitchProfileStorageError,
    TwitchProfileValidationError,
    VodConflictError,
    VodNotFoundError,
    VodPipelineStorage,
    VodStatus,
    VodValidationError,
)
from vod_pipeline.service import (
    VodPipelineService,
    parse_twitch_video_url,
)


# ---------------------------------------------------------------------------
# Profile persistence
# ---------------------------------------------------------------------------


def test_create_profile_persists_login_and_channel_url(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    assert profile["login"] == "casepayt"
    assert profile["channel_url"] == "https://www.twitch.tv/casepayt"
    assert profile["display_name"] == "casepayt"
    assert profile["id"]  # internal uuid
    # File on disk.
    path = vod_service.storage._profile_path(profile["id"])
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["login"] == "casepayt"
    # No secret in the persisted file.
    assert "client_secret" not in on_disk
    assert "token" not in on_disk


def test_create_profile_from_channel_url(vod_service, channel_lister):
    profile = vod_service.create_profile("https://www.twitch.tv/casepayt")
    assert profile["login"] == "casepayt"


def test_create_profile_invalid_url_rejected(vod_service):
    with pytest.raises(TwitchProfileValidationError):
        vod_service.create_profile("https://youtube.com/flekay")
    with pytest.raises(TwitchProfileValidationError):
        vod_service.create_profile("https://www.twitch.tv/videos/123")
    with pytest.raises(TwitchProfileValidationError):
        vod_service.create_profile("   ")


def test_create_profile_duplicate_login(vod_service, channel_lister):
    vod_service.create_profile("casepayt")
    with pytest.raises(TwitchProfileConflictError):
        vod_service.create_profile("casepayt")


def test_list_profiles(vod_service, channel_lister):
    vod_service.create_profile("casepayt")
    vod_service.create_profile("other")
    profiles = vod_service.list_profiles()
    assert len(profiles) == 2
    assert {p["login"] for p in profiles} == {"casepayt", "other"}


def test_refresh_profile_updates_timestamp(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    refreshed = vod_service.refresh_profile(profile["id"])
    assert refreshed["id"] == profile["id"]
    assert refreshed["login"] == "casepayt"


def test_refresh_profile_unknown_id(vod_service):
    with pytest.raises(TwitchProfileNotFoundError):
        vod_service.refresh_profile("00000000-0000-0000-0000-000000000000")


def test_delete_profile_without_vods(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    assert vod_service.delete_profile(profile["id"]) is True
    with pytest.raises(TwitchProfileNotFoundError):
        vod_service.get_profile(profile["id"])


def test_delete_profile_deletes_vods(vod_service, channel_lister):
    """Deleting a profile deletes VOD metadata but library items survive."""
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    vods = vod_service.list_vods(profile_id=profile["id"])
    assert len(vods) == 1
    assert vod_service.delete_profile(profile["id"]) is True
    # VOD metadata is gone with the profile.
    all_vods = vod_service.list_vods()
    assert len(all_vods) == 0


def test_sync_auto_links_library_item(vod_service_with_library, channel_lister):
    """Re-syncing after profile deletion auto-links already-downloaded VODs.

    When a VOD's twitch_video_id is already in the library (downloaded
    before, profile was deleted), the next sync marks the VOD as READY
    and links the library item back — no need to click download again.
    """
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service_with_library.create_profile("casepayt")
    vod_service_with_library.sync_vods(profile["id"])
    vods = vod_service_with_library.list_vods(profile_id=profile["id"])
    vod_id = vods[0]["id"]

    # Simulate: the VOD was downloaded and promoted to the library.
    from library import LibraryService, LibraryStorage
    lib_service = vod_service_with_library.library_service
    # Create a library item for this VOD (as promote_vod_file would).
    item = lib_service.create_item(
        source="vod",
        title="Test VOD",
        file_name="source.mp4",
        container="mp4",
        twitch_video_id="100",
        vod_id=vod_id,
    )
    # Link the VOD to the library item.
    vods[0]["library_item_id"] = item["id"]
    vods[0]["status"] = VodStatus.READY.value
    vod_service_with_library.storage.save_vod(vods[0])

    # Delete the profile → VOD metadata gone, library item survives with vod_id=null.
    vod_service_with_library.delete_profile(profile["id"])
    assert len(vod_service_with_library.list_vods()) == 0
    # Library item still exists, vod_id is nulled.
    lib_item = lib_service.get_item(item["id"])
    assert lib_item["vod_id"] is None

    # Re-create the profile and sync again.
    profile2 = vod_service_with_library.create_profile("casepayt")
    vod_service_with_library.sync_vods(profile2["id"])
    vods2 = vod_service_with_library.list_vods(profile_id=profile2["id"])
    assert len(vods2) == 1
    # The VOD should be auto-linked: status READY, library_item_id set.
    assert vods2[0]["status"] == VodStatus.READY.value
    assert vods2[0]["library_item_id"] == item["id"]
    # The library item's vod_id should now point to the new VOD.
    lib_item2 = lib_service.get_item(item["id"])
    assert lib_item2["vod_id"] == vods2[0]["id"]


def test_atomic_persistence_tmp_not_treated_as_profile(vod_data_dir):
    storage = VodPipelineStorage(vod_data_dir)
    pid = "11111111-1111-1111-1111-111111111111"
    profile = {
        "schema_version": 1,
        "id": pid,
        "login": "a",
        "channel_url": "https://www.twitch.tv/a",
        "display_name": "A",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "last_synced_at": None,
    }
    storage.save_profile(profile)
    # A stray .tmp file must not be listed as a profile.
    tmp = storage._profile_path(pid).with_name("profile.json.tmp")
    tmp.write_text("garbage", encoding="utf-8")
    profiles = list(storage.iter_profiles())
    assert len(profiles) == 1


def test_corrupt_profile_file_does_not_break_listing(vod_data_dir):
    storage = VodPipelineStorage(vod_data_dir)
    pid = "22222222-2222-2222-2222-222222222222"
    pdir = storage.profiles_dir / pid
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.json").write_text("{not valid json", encoding="utf-8")
    # Listing skips the corrupt file instead of raising.
    profiles = list(storage.iter_profiles())
    assert profiles == []


def test_profile_path_traversal_blocked(vod_data_dir):
    storage = VodPipelineStorage(vod_data_dir)
    with pytest.raises(TwitchProfileStorageError):
        storage.load_profile("..%2f..%2fetc")
    with pytest.raises(TwitchProfileStorageError):
        storage.load_profile("not-a-uuid")


# ---------------------------------------------------------------------------
# VOD sync
# ---------------------------------------------------------------------------


def test_sync_creates_new_vods(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    channel_lister.add_vod("casepayt", "101")
    profile = vod_service.create_profile("casepayt")
    result = vod_service.sync_vods(profile["id"])
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["unchanged"] == 0
    assert result["total"] == 2
    vods = vod_service.list_vods(profile_id=profile["id"])
    assert len(vods) == 2
    # Sync does not start downloads.
    assert all(v["status"] == VodStatus.DISCOVERED.value for v in vods)


def test_sync_deduplicates_by_twitch_video_id(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    result = vod_service.sync_vods(profile["id"])
    assert result["created"] == 0
    assert result["unchanged"] == 1
    vods = vod_service.list_vods(profile_id=profile["id"])
    assert len(vods) == 1


def test_sync_updates_changed_metadata(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100", title="Old")
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    channel_lister.vods_by_login["casepayt"][0]["title"] = "New Title"
    result = vod_service.sync_vods(profile["id"])
    assert result["updated"] == 1
    vods = vod_service.list_vods(profile_id=profile["id"])
    assert vods[0]["title"] == "New Title"


def test_sync_does_not_touch_download_state(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    vod = vod_service.list_vods(profile_id=profile["id"])[0]
    vod["status"] = VodStatus.READY.value
    vod["download"]["file_name"] = "source.mp4"
    vod_service.storage.save_vod(vod)
    vod_service.sync_vods(profile["id"])
    vod = vod_service.list_vods(profile_id=profile["id"])[0]
    assert vod["status"] == VodStatus.READY.value
    assert vod["download"]["file_name"] == "source.mp4"


def test_sync_includes_clips(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    channel_lister.add_clip("casepayt", "ClipSlug1")
    profile = vod_service.create_profile("casepayt")
    result = vod_service.sync_vods(profile["id"])
    assert result["total"] == 2
    vods = vod_service.list_vods(profile_id=profile["id"])
    types = {v["type"] for v in vods}
    assert types == {"archive", "clip"}


def test_sync_updates_last_synced_at(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    assert profile["last_synced_at"] is None
    vod_service.sync_vods(profile["id"])
    refreshed = vod_service.get_profile(profile["id"])
    assert refreshed["last_synced_at"] is not None


def test_sync_ytdlp_error(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    channel_lister.fail_vods_next = True
    with pytest.raises(TwitchClientError):
        vod_service.sync_vods(profile["id"])


# ---------------------------------------------------------------------------
# Manual VOD / clip import
# ---------------------------------------------------------------------------


def test_parse_twitch_video_url_valid_vod():
    assert parse_twitch_video_url("https://www.twitch.tv/videos/1234567890") == ("1234567890", "archive")
    assert parse_twitch_video_url("http://twitch.tv/videos/1") == ("1", "archive")


def test_parse_twitch_video_url_valid_clip():
    assert parse_twitch_video_url("https://www.twitch.tv/casepayt/clip/SomeSlug-abc123")[1] == "clip"
    assert parse_twitch_video_url("https://clips.twitch.tv/SomeSlug-abc123")[1] == "clip"


def test_parse_twitch_video_url_rejects_bad_inputs():
    with pytest.raises(VodValidationError):
        parse_twitch_video_url("https://www.twitch.tv/flekay")
    with pytest.raises(VodValidationError):
        parse_twitch_video_url("https://youtube.com/watch?v=1")
    with pytest.raises(VodValidationError):
        parse_twitch_video_url("")


def test_import_vod_valid(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    vod = vod_service.import_vod("https://www.twitch.tv/videos/100", profile_id=profile["id"])
    assert vod["twitch_video_id"] == "100"
    assert vod["profile_id"] == profile["id"]
    assert vod["status"] == VodStatus.DISCOVERED.value
    assert vod["type"] == "archive"


def test_import_vod_without_profile(vod_service, channel_lister):
    """VODs can be imported without a profile."""
    channel_lister.add_vod("casepayt", "100")
    vod = vod_service.import_vod("https://www.twitch.tv/videos/100")
    assert vod["twitch_video_id"] == "100"
    assert vod["profile_id"] is None
    assert vod["status"] == VodStatus.DISCOVERED.value


def test_import_clip_valid(vod_service, channel_lister):
    channel_lister.add_clip("casepayt", "ClipSlug1")
    profile = vod_service.create_profile("casepayt")
    vod = vod_service.import_vod(
        "https://www.twitch.tv/casepayt/clip/ClipSlug1", profile_id=profile["id"]
    )
    assert vod["type"] == "clip"
    assert vod["status"] == VodStatus.DISCOVERED.value


def test_import_vod_duplicate_returns_existing(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    first = vod_service.import_vod("https://www.twitch.tv/videos/100", profile_id=profile["id"])
    second = vod_service.import_vod("https://www.twitch.tv/videos/100", profile_id=profile["id"])
    assert first["id"] == second["id"]


def test_import_vod_invalid_url(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    with pytest.raises(VodValidationError):
        vod_service.import_vod("https://youtube.com/watch?v=1", profile_id=profile["id"])


def test_import_vod_not_found(vod_service, channel_lister):
    profile = vod_service.create_profile("casepayt")
    with pytest.raises(VodValidationError):
        vod_service.import_vod("https://www.twitch.tv/videos/999999", profile_id=profile["id"])


# ---------------------------------------------------------------------------
# VOD list / sort / filter
# ---------------------------------------------------------------------------


def test_list_vods_sort_and_filter(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "1", title="Alpha", duration=1000.0)
    channel_lister.add_vod("casepayt", "2", title="Beta", duration=5000.0)
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    # Mark one READY to test status filter.
    vods = vod_service.list_vods(profile_id=profile["id"])
    ready = vods[0]
    ready["status"] = VodStatus.READY.value
    vod_service.storage.save_vod(ready)
    only_ready = vod_service.list_vods(profile_id=profile["id"], status=VodStatus.READY.value)
    assert len(only_ready) == 1
    # Search by title.
    hits = vod_service.list_vods(profile_id=profile["id"], search="alpha")
    assert len(hits) == 1
    # Sort longest first.
    longest = vod_service.list_vods(profile_id=profile["id"], sort="longest")
    assert len(longest) == 2


def test_get_vod_unknown_id(vod_service):
    with pytest.raises(VodNotFoundError):
        vod_service.get_vod("00000000-0000-0000-0000-000000000000")
