"""Unit tests for Graph calendar and SharePoint download orchestration."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from xibo_sync.config import load_config
from xibo_sync.graph_download import calendar_window, download_calendar, download_media, run_download_phase


class FakeGraph:
    def __init__(self, items=None, events=None, error=None):
        self.items = items or []
        self.events = events or []
        self.error = error
        self.calls = []
        self.download_calls = []
        self.authenticated = False

    def authenticate(self):
        if self.error == "auth":
            raise RuntimeError("auth failed")
        self.authenticated = True

    def get_paged(self, path, params=None):
        self.calls.append((path, params))
        if self.error == path:
            raise RuntimeError("request failed")
        if "calendarView" in path:
            return "context", self.events
        return None, self.items

    def download(self, item, site_id, dest):
        self.download_calls.append(item)
        dest.write_bytes(item.get("bytes", b"data"))


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("CMS_BASE_URL", "http://cms.test")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("SHAREPOINT_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("SYNC_MODE", "upload")
    monkeypatch.setenv("CALENDAR_JSON_PATH", str(tmp_path / "calendar"))
    monkeypatch.setenv("GRAPH_SHAREPOINT_SITE_ID", "site-id")
    monkeypatch.setenv("GRAPH_CALENDAR_USER_ID", "user@example.test")
    monkeypatch.setenv("GRAPH_CALENDAR_DAYS_AHEAD", "14")
    monkeypatch.setenv("GRAPH_CALENDAR_KEEP_SNAPSHOTS", "2")
    return load_config(sync_mode="upload")


def test_calendar_window_uses_configured_timezone_offsets():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("UTC"))

    start, end = calendar_window(now, 30, 14, "Europe/Berlin")

    assert start.tzinfo == ZoneInfo("Europe/Berlin")
    assert (end - start).days == 44
    assert start.hour == 14


def test_download_calendar_writes_graph_snapshot_readable_by_loader(cfg, tmp_path):
    graph = FakeGraph(events=[{"id": "event-1", "subject": "Planning"}])
    now = datetime(2026, 10, 6, 12, 30, 45, tzinfo=ZoneInfo("Europe/Berlin"))

    snapshot = download_calendar(graph, cfg, tmp_path, now, False)

    assert snapshot == tmp_path / "calendar" / "office_calendar_events_2026-10-06_12-30-45.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == {
        "@odata.context": "context", "value": [{"id": "event-1", "subject": "Planning"}]
    }
    assert graph.calls[0][0] == "/users/user@example.test/calendarView"
    assert graph.calls[0][1]["startDateTime"].endswith("+02:00")


def test_calendar_prunes_only_old_matching_snapshots(cfg, tmp_path):
    directory = tmp_path / "calendar"
    directory.mkdir()
    old = directory / "office_calendar_events_2026-10-01_00-00-00.json"
    keep = directory / "office_calendar_events_2026-10-05_00-00-00.json"
    unrelated = directory / "other.json"
    for path in (old, keep, unrelated):
        path.write_text("{}", encoding="utf-8")
    cfg.graph_calendar_keep_snapshots = 2
    now = datetime(2026, 10, 6, tzinfo=ZoneInfo("Europe/Berlin"))

    download_calendar(FakeGraph(), cfg, tmp_path, now, False)

    assert not old.exists()
    assert keep.exists()
    assert unrelated.exists()


def test_calendar_rejects_existing_file_path(cfg, tmp_path):
    file_path = tmp_path / "calendar-file.json"
    file_path.write_text("{}", encoding="utf-8")
    cfg.calendar_json_path = file_path

    with pytest.raises(RuntimeError, match="directory"):
        download_calendar(FakeGraph(), cfg, tmp_path, datetime.now(), False)


def test_media_download_tracks_skips_and_deletes_only_owned_files(cfg, tmp_path):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    (directory / "stable.png").write_bytes(b"same")
    (directory / "manual.png").write_bytes(b"manual")
    (directory / "stale.jpg").write_bytes(b"stale")
    manifest = {
        "stable.png": {"id": "stable", "eTag": "tag1", "size": 4, "lastModifiedDateTime": "time"},
        "stale.jpg": {"id": "gone", "eTag": "tag", "size": 5, "lastModifiedDateTime": "old"},
    }
    (directory / ".sharepoint_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    graph = FakeGraph(items=[
        {"id": "stable", "name": "stable.png", "file": {}, "eTag": "tag1", "size": 4, "lastModifiedDateTime": "time"},
        {"id": "new", "name": "new.PNG", "file": {}, "eTag": "tag2", "size": 4, "lastModifiedDateTime": "new", "bytes": b"new!"},
        {"id": "manual", "name": "manual.png", "file": {}, "eTag": "tag3", "size": 6, "lastModifiedDateTime": "new"},
        {"id": "folder", "name": "folder", "folder": {}},
        {"id": "text", "name": "notes.txt", "file": {}},
    ])

    result = download_media(graph, cfg, False)

    assert result.downloaded == 1
    assert result.skipped == 1
    assert result.deleted == 1
    assert result.ignored == 1
    assert result.errors == []
    assert graph.download_calls[0]["name"] == "new.PNG"
    assert (directory / "manual.png").read_bytes() == b"manual"
    assert not (directory / "stale.jpg").exists()
    assert json.loads((directory / ".sharepoint_manifest.json").read_text(encoding="utf-8"))["new.PNG"]["id"] == "new"


def test_unchanged_file_refreshes_manifest_item_id_without_redownloading(cfg):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    (directory / "same.png").write_bytes(b"data")
    (directory / ".sharepoint_manifest.json").write_text(
        json.dumps({"same.png": {"id": "old-id", "eTag": "tag", "size": 4, "lastModifiedDateTime": "time"}}),
        encoding="utf-8",
    )
    graph = FakeGraph(items=[
        {"id": "new-id", "name": "same.png", "file": {}, "eTag": "tag", "size": 4, "lastModifiedDateTime": "time"}
    ])

    result = download_media(graph, cfg, False)

    manifest = json.loads((directory / ".sharepoint_manifest.json").read_text(encoding="utf-8"))
    assert result.skipped == 1
    assert graph.download_calls == []
    assert manifest["same.png"]["id"] == "new-id"
    assert (directory / "same.png").exists()


def test_changed_manifest_etag_redownloads_file(cfg):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    existing_file = directory / "changed.png"
    existing_file.write_bytes(b"old")
    (directory / ".sharepoint_manifest.json").write_text(
        json.dumps({"changed.png": {
            "id": "item",
            "eTag": "old-tag",
            "size": 3,
            "lastModifiedDateTime": "old-time",
        }}),
        encoding="utf-8",
    )
    graph = FakeGraph(items=[{
        "id": "item",
        "name": "changed.png",
        "file": {},
        "eTag": "new-tag",
        "size": 3,
        "lastModifiedDateTime": "old-time",
        "bytes": b"new",
    }])

    result = download_media(graph, cfg, False)

    assert result.downloaded == 1
    assert result.skipped == 0
    assert graph.download_calls == [graph.items[0]]
    assert existing_file.read_bytes() == b"new"
    assert json.loads((directory / ".sharepoint_manifest.json").read_text(encoding="utf-8"))[
        "changed.png"]["eTag"] == "new-tag"


def test_manifest_entry_is_kept_when_listed_item_extension_is_excluded(cfg):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    tracked_file = directory / "archive.txt"
    tracked_file.write_bytes(b"keep")
    manifest_path = directory / ".sharepoint_manifest.json"
    manifest_path.write_text(
        json.dumps({"archive.txt": {"id": "still-present", "eTag": "tag"}}),
        encoding="utf-8",
    )
    cfg.media_extensions = (".png",)
    graph = FakeGraph(items=[
        {"id": "still-present", "name": "archive.txt", "file": {}}
    ])

    result = download_media(graph, cfg, False)

    assert result.deleted == 0
    assert tracked_file.read_bytes() == b"keep"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "archive.txt": {"id": "still-present", "eTag": "tag"}
    }


@pytest.mark.parametrize("name", ["../evil.png", "a/b.png", "a\\b.png"])
def test_media_download_reports_unsafe_names_without_writing_outside(cfg, tmp_path, name):
    graph = FakeGraph(items=[
        {"id": "unsafe", "name": name, "file": {}},
        {"id": "valid", "name": "good.png", "file": {}, "size": 4},
    ])

    result = download_media(graph, cfg, False)

    assert len(result.errors) == 1
    assert result.downloaded == 1
    assert not (tmp_path / "evil.png").exists()


def test_media_folder_path_is_encoded_and_dry_run_writes_nothing(cfg, tmp_path):
    cfg.graph_sharepoint_folder = "Images #1?/sub"
    graph = FakeGraph(items=[{"id": "image", "name": "image.png", "file": {}, "size": 4}])

    result = download_media(graph, cfg, True)

    assert graph.calls[0][0] == "/sites/site-id/drive/root:/Images%20%231%3F/sub:/children"
    assert result.downloaded == 1
    assert not cfg.sharepoint_media_dir.exists()


def test_media_download_never_treats_manifest_name_as_remote_media(cfg):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    manifest_path = directory / ".sharepoint_manifest.json"
    manifest_path.write_text(
        json.dumps({".sharepoint_manifest.json": {"id": "remote-manifest"}}),
        encoding="utf-8",
    )
    cfg.media_extensions = (*cfg.media_extensions, ".json")
    graph = FakeGraph(items=[
        {"id": "remote-manifest", "name": ".sharepoint_manifest.json", "file": {}, "size": 2}
    ])

    result = download_media(graph, cfg, False)

    assert result.ignored == 1
    assert graph.download_calls == []
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {}


def test_listing_failure_does_not_delete_stale_files(cfg):
    directory = cfg.sharepoint_media_dir
    directory.mkdir()
    stale = directory / "stale.png"
    stale.write_bytes(b"keep on failed listing")
    (directory / ".sharepoint_manifest.json").write_text(
        json.dumps({"stale.png": {"id": "gone"}}), encoding="utf-8"
    )
    graph = FakeGraph(error="/sites/site-id/drive/root:/test_images:/children")

    with pytest.raises(RuntimeError, match="request failed"):
        download_media(graph, cfg, False)

    assert stale.exists()


def test_dry_run_preserves_existing_calendar_and_media_state(cfg, tmp_path):
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir()
    old_snapshot = calendar_dir / "office_calendar_events_2026-10-01_00-00-00.json"
    old_snapshot.write_text("old snapshot", encoding="utf-8")

    media_dir = cfg.sharepoint_media_dir
    media_dir.mkdir()
    stale_media = media_dir / "stale.png"
    stale_media.write_bytes(b"stale")
    manifest_path = media_dir / ".sharepoint_manifest.json"
    manifest_path.write_text(
        json.dumps({"stale.png": {"id": "missing", "eTag": "old"}}),
        encoding="utf-8",
    )
    snapshot_before = old_snapshot.read_bytes()
    media_before = stale_media.read_bytes()
    manifest_before = manifest_path.read_bytes()
    cfg.graph_calendar_keep_snapshots = 1
    graph = FakeGraph(
        events=[{"id": "event-1", "subject": "Planning"}],
        items=[{"id": "new", "name": "new.png", "file": {}, "size": 4}],
    )
    now = datetime(2026, 10, 6, tzinfo=ZoneInfo("Europe/Berlin"))

    snapshot = download_calendar(graph, cfg, tmp_path, now, True)
    media = download_media(graph, cfg, True)

    assert snapshot is None
    assert len(graph.calls) == 2
    assert media.downloaded == 1
    assert media.deleted == 1
    assert sorted(path.name for path in calendar_dir.iterdir()) == [old_snapshot.name]
    assert old_snapshot.read_bytes() == snapshot_before
    assert stale_media.read_bytes() == media_before
    assert manifest_path.read_bytes() == manifest_before
    assert not (media_dir / "new.png").exists()


def test_calendar_and_media_download_failures_are_independent(cfg, tmp_path, monkeypatch):
    import xibo_sync.graph_download as module

    graph = FakeGraph()
    calls = []

    def fail_calendar(*_args, **_kwargs):
        calls.append("calendar")
        raise RuntimeError("calendar failed")

    def succeed_media(*_args, **_kwargs):
        calls.append("media")
        return module.MediaDownloadResult()

    monkeypatch.setattr(module, "download_calendar", fail_calendar)
    monkeypatch.setattr(module, "download_media", succeed_media)
    result = run_download_phase(cfg, tmp_path, graph=graph)

    assert calls == ["calendar", "media"]
    assert result.errors == ["Calendar download failed: calendar failed"]

    calls.clear()

    def succeed_calendar(*_args, **_kwargs):
        calls.append("calendar")
        return None

    def fail_media(*_args, **_kwargs):
        calls.append("media")
        raise RuntimeError("media failed")

    monkeypatch.setattr(module, "download_calendar", succeed_calendar)
    monkeypatch.setattr(module, "download_media", fail_media)
    result = run_download_phase(cfg, tmp_path, graph=graph)

    assert calls == ["calendar", "media"]
    assert result.errors == ["SharePoint media download failed: media failed"]


def test_run_download_phase_auth_failure_skips_both_downloads(cfg, tmp_path):
    graph = FakeGraph(error="auth")

    result = run_download_phase(cfg, tmp_path, graph=graph)

    assert result.errors == ["auth failed"]
    assert graph.calls == []