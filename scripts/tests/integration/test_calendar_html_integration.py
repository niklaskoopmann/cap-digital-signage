"""Integration tests for run_calendar_html_upload orchestration in app.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from xibo_sync import app
from xibo_sync.config import load_config


class FakeXiboClient:
    """Records calls instead of making real HTTP requests to Xibo."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        self.calls: list[tuple[str, Any]] = []

    def authenticate_oauth(self, client_id: str, client_secret: str) -> None:
        self.calls.append(("authenticate_oauth", (client_id, client_secret)))

    def health_check(self) -> None:
        self.calls.append(("health_check", None))

    def upload_media(self, **kwargs: Any) -> dict:
        self.calls.append(("upload_media", kwargs))
        return {"mediaId": "media-101", "name": kwargs["name"]}

    def create_fullscreen_layout(self, media: dict, dry_run: bool) -> dict:
        self.calls.append(("create_fullscreen_layout", (media, dry_run)))
        return {"layoutId": "draft-layout-201", "layout": f'{media["name"]} fullscreen'}

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))

    def get_layout_by_name(self, name: str) -> dict:
        self.calls.append(("get_layout_by_name", name))
        return {"layoutId": "published-layout-201", "layout": name}

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))

    def assign_layouts_to_displaygroup(
        self,
        display_group_id: str,
        layout_ids: list[str],
        dry_run: bool = False,
    ) -> None:
        self.calls.append(("assign_layouts_to_displaygroup", (display_group_id, layout_ids, dry_run)))

    def change_layout_on_displaygroup(
        self,
        display_group_id: str,
        layout_id: str,
        download_required: int,
        dry_run: bool = False,
    ) -> None:
        self.calls.append(
            ("change_layout_on_displaygroup", (display_group_id, layout_id, download_required, dry_run))
        )

def _make_event(subject: str, start_iso: str, end_iso: str) -> dict:
    """Build a minimal Microsoft Graph-style calendar event."""
    return {
        "id": f"evt-{subject.replace(' ', '-').lower()}",
        "subject": subject,
        "start": {"dateTime": start_iso, "timeZone": "UTC"},
        "end": {"dateTime": end_iso, "timeZone": "UTC"},
        "isCancelled": False,
    }


@pytest.fixture()
def calendar_snapshot_dir(tmp_path: Path) -> Path:
    """Write a snapshot containing 2 future events (in UTC, covering today)."""
    events = [
        _make_event("Morning Stand-up", "2099-01-01T09:00:00", "2099-01-01T09:30:00"),
        _make_event("Planning Session", "2099-01-01T14:00:00", "2099-01-01T15:00:00"),
    ]
    snapshot = tmp_path / "office_calendar_events_2099-01-01_08-00-00.json"
    snapshot.write_text(json.dumps({"value": events}), encoding="utf-8")
    return tmp_path


def _setup_env(
    monkeypatch: pytest.MonkeyPatch,
    calendar_dir: Path,
    template_dir: Path,
    *,
    dry_run: bool = False,
) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://192.168.1.1")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", "../media")
    monkeypatch.setenv("CALENDAR_JSON_PATH", str(calendar_dir))
    monkeypatch.setenv("CALENDAR_ENABLE_HTML", "true")
    monkeypatch.setenv("CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks")
    monkeypatch.setenv("CALENDAR_TEMPLATE_DIR", str(template_dir))
    monkeypatch.setenv("CALENDAR_AUTO_PUBLISH", "true")
    monkeypatch.setenv("CALENDAR_TIMEZONE", "UTC")
    monkeypatch.setenv("CALENDAR_PACKAGE_RETENTION_DAYS", "30")
    monkeypatch.setenv("DRY_RUN", "true" if dry_run else "false")
    monkeypatch.setenv("CREATE_LAYOUT_PER_UPLOAD", "false")
    monkeypatch.setenv("ASSIGN_LAYOUT_ON_CHANGE", "false")
    monkeypatch.setenv("IMMEDIATE_SHOW_ON_CHANGE", "false")
    monkeypatch.delenv("DISPLAY_GROUP_ID", raising=False)
    monkeypatch.delenv("CALENDAR_DATASET_CODE", raising=False)
    monkeypatch.delenv("TRIGGER_COLLECTNOW_ON_CHANGES", raising=False)


def test_run_calendar_html_upload_generates_and_uploads_all_views(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """Three images (one per view) must be generated and uploaded as media."""
    # Use the bundled template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir)
    cfg = load_config()
    # Use tmp_path as scripts_dir so packages land in a clean directory.
    cfg.calendar_json_path = calendar_snapshot_dir

    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    def renderer(html: str, path: Path, width: int, height: int) -> None:
        assert (width, height) == (1920, 1080)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    exit_code = app.run_calendar_html_upload(cfg, tmp_path, renderer=renderer)

    assert exit_code == 0

    client = fake_holder["client"]
    upload_calls = [c for c in client.calls if c[0] == "upload_media"]

    assert len(upload_calls) == 3, (
        f"Expected 3 upload calls (one per view), got {len(upload_calls)}"
    )
    required_tag_prefix = {cfg.managed_tag, "calendar-html", "calendar-image"}
    for call in upload_calls:
        upload = call[1]
        filename = upload["file_path"].name
        assert filename.startswith("calendar_") and filename.endswith(".png")
        view_type, start_date = filename.removeprefix("calendar_").removesuffix(".png").rsplit("_", 1)
        assert upload["tags"][:3] == [cfg.managed_tag, "calendar-html", "calendar-image"]
        assert set(upload["tags"]) == required_tag_prefix | {f"calendar-{view_type}", start_date}
        assert upload["file_path"].parent == (tmp_path / "../media").resolve()
    layout_mutations = {
        "create_fullscreen_layout",
        "publish_layout",
        "tag_layout",
        "assign_layouts_to_displaygroup",
        "change_layout_on_displaygroup",
    }
    assert layout_mutations.isdisjoint(name for name, _details in client.calls)


def test_run_calendar_html_upload_uses_normal_layout_lifecycle_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """A generated image follows the normal layout lifecycle when enabled."""
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir)
    monkeypatch.setenv("CALENDAR_HTML_VIEWS", "today")
    monkeypatch.setenv("CREATE_LAYOUT_PER_UPLOAD", "true")
    monkeypatch.setenv("ASSIGN_LAYOUT_ON_CHANGE", "true")
    monkeypatch.setenv("IMMEDIATE_SHOW_ON_CHANGE", "true")
    monkeypatch.setenv("DISPLAY_GROUP_ID", "group-301")
    cfg = load_config()
    cfg.calendar_json_path = calendar_snapshot_dir

    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    def renderer(html: str, path: Path, width: int, height: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    assert app.run_calendar_html_upload(cfg, tmp_path, renderer=renderer) == 0

    calls = fake_holder["client"].calls
    call_names = [name for name, _details in calls]
    assert call_names == [
        "health_check",
        "upload_media",
        "create_fullscreen_layout",
        "publish_layout",
        "get_layout_by_name",
        "tag_layout",
        "publish_layout",
        "get_layout_by_name",
        "assign_layouts_to_displaygroup",
        "change_layout_on_displaygroup",
    ]

    upload = next(details for name, details in calls if name == "upload_media")
    assert upload["tags"][:4] == [cfg.managed_tag, "calendar-html", "calendar-image", "calendar-today"]
    created_layout = next(details for name, details in calls if name == "create_fullscreen_layout")
    assert created_layout[0]["name"].startswith("calendar_today_")
    assert all(
        details != "Calendar - Today"
        for name, details in calls
        if name == "get_layout_by_name"
    )
    tag_call = next(details for name, details in calls if name == "tag_layout")
    assert tag_call == (
        "published-layout-201",
        [cfg.managed_tag, "xibo-sync-media:media-101"],
        False,
    )
    assert calls[-2] == (
        "assign_layouts_to_displaygroup",
        ("group-301", ["published-layout-201"], False),
    )
    assert calls[-1] == (
        "change_layout_on_displaygroup",
        ("group-301", "published-layout-201", 1, False),
    )


def test_run_calendar_html_upload_propagates_upload_verification_failure(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """Calendar uploads inherit verification failure handling from upload_media."""
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir)
    monkeypatch.setenv("CALENDAR_HTML_VIEWS", "today")
    monkeypatch.setenv("CREATE_LAYOUT_PER_UPLOAD", "true")
    cfg = load_config()
    cfg.calendar_json_path = calendar_snapshot_dir

    fake_holder: dict[str, FakeXiboClient] = {}

    class VerificationFailureClient(FakeXiboClient):
        def upload_media(self, **kwargs: Any) -> dict:
            self.calls.append(("upload_media", kwargs))
            raise RuntimeError("accepted, but mediaId=media-101 is not present in the Xibo library")

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> VerificationFailureClient:
        client = VerificationFailureClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    def renderer(html: str, path: Path, width: int, height: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    with pytest.raises(RuntimeError, match="not present in the Xibo library"):
        app.run_calendar_html_upload(cfg, tmp_path, renderer=renderer)

    assert [name for name, _details in fake_holder["client"].calls] == [
        "health_check",
        "upload_media",
    ]


def test_run_calendar_html_upload_dry_run_skips_writes_and_cms(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """In dry-run mode no PNGs or CMS client calls should be made."""
    # Use the bundled template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir, dry_run=True)
    cfg = load_config()
    cfg.dry_run = True
    cfg.calendar_json_path = calendar_snapshot_dir

    monkeypatch.setattr(app, "XiboClient", lambda *args: pytest.fail("dry-run must not construct client"))
    reported: list[str] = []
    monkeypatch.setattr(app, "ui_ok", reported.append)
    exit_code = app.run_calendar_html_upload(cfg, tmp_path, renderer=pytest.fail)

    assert exit_code == 0

    assert not (tmp_path / "calendar_packages").exists()
    expected_media_dir = (tmp_path / "../media").resolve()
    expected_date = datetime.now(timezone.utc).date().isoformat()
    dry_run_messages = [message for message in reported if "Would render and upload" in message]
    assert len(dry_run_messages) == 3
    assert all(
        str(expected_media_dir / f"calendar_{view}_{expected_date}.png") in " ".join(dry_run_messages)
        for view in ("today", "this_week", "next_2_weeks")
    )


def test_run_calendar_html_upload_renderer_failure_precedes_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """A renderer failure must stop before constructing or calling the CMS client."""
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir)
    cfg = load_config()
    cfg.calendar_json_path = calendar_snapshot_dir

    monkeypatch.setattr(app, "XiboClient", lambda *args: pytest.fail("renderer failure must precede client construction"))

    def renderer(*args: Any) -> None:
        raise RuntimeError("calendar PNG rendering failed")

    with pytest.raises(RuntimeError, match="calendar PNG rendering failed"):
        app.run_calendar_html_upload(cfg, tmp_path, renderer=renderer)


def test_run_calendar_html_upload_fails_fast_when_view_config_missing(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """If any configured view is missing, fail before constructing/calling Xibo client."""
    template_dir = tmp_path / "template"
    views_dir = template_dir / "views"
    views_dir.mkdir(parents=True)
    (template_dir / "template.html").write_text("<html>{{ title }}</html>", encoding="utf-8")
    (views_dir / "today.json").write_text(
        json.dumps({"title": "Today", "window_days": 1, "layout_name": "Calendar Today"}),
        encoding="utf-8",
    )

    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir)
    monkeypatch.setenv("CALENDAR_HTML_VIEWS", "today,missing_view")
    cfg = load_config()
    cfg.calendar_json_path = calendar_snapshot_dir

    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    exit_code = app.run_calendar_html_upload(cfg, tmp_path)

    assert exit_code == 2
    assert "client" not in fake_holder


def test_run_calendar_html_upload_berlin_timezone_renders_local_times(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generated HTML with Berlin timezone should show Berlin-local times for UTC events.

    Uses today's date (in Berlin time) so the "today" view keeps matching the event
    regardless of when the suite runs, and derives the expected local time from the
    UTC event time via zoneinfo instead of assuming a fixed CEST/CET offset.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    today_berlin = datetime.now(berlin).date()
    event_date = today_berlin.isoformat()

    utc_start = datetime.fromisoformat(f"{event_date}T12:00:00").replace(tzinfo=ZoneInfo("UTC"))
    expected_local_time = utc_start.astimezone(berlin).strftime("%H:%M")

    # Create a calendar snapshot with UTC events
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir()

    events = [
        _make_event("Summer meeting", f"{event_date}T12:00:00", f"{event_date}T13:00:00"),
    ]
    snapshot = calendar_dir / f"office_calendar_events_{event_date}_10-00-00.json"
    snapshot.write_text(json.dumps({"value": events}), encoding="utf-8")
    
    # Use the bundled template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    if not template_dir.exists():
        pytest.skip(f"Bundled template directory not found: {template_dir}")
    
    # Setup with Berlin timezone explicitly
    monkeypatch.setenv("CMS_BASE_URL", "http://192.168.1.1")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", "../media")
    monkeypatch.setenv("CALENDAR_JSON_PATH", str(calendar_dir))
    monkeypatch.setenv("CALENDAR_ENABLE_HTML", "true")
    monkeypatch.setenv("CALENDAR_HTML_VIEWS", "today")
    monkeypatch.setenv("CALENDAR_TEMPLATE_DIR", str(template_dir))
    monkeypatch.setenv("CALENDAR_AUTO_PUBLISH", "true")
    monkeypatch.setenv("CALENDAR_TIMEZONE", "Europe/Berlin")  # Berlin timezone
    monkeypatch.setenv("CALENDAR_PACKAGE_RETENTION_DAYS", "30")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("DISPLAY_GROUP_ID", raising=False)
    monkeypatch.delenv("CALENDAR_DATASET_CODE", raising=False)
    monkeypatch.delenv("TRIGGER_COLLECTNOW_ON_CHANGES", raising=False)
    
    cfg = load_config()
    cfg.calendar_json_path = calendar_dir
    
    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    rendered_html: list[str] = []

    def renderer(html: str, path: Path, width: int, height: int) -> None:
        rendered_html.append(html)
        assert (width, height) == (1920, 1080)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    exit_code = app.run_calendar_html_upload(cfg, tmp_path, renderer=renderer)

    assert exit_code == 0

    assert expected_local_time in rendered_html[0]
    utc_time = "12:00"
    if utc_time != expected_local_time:
        assert utc_time not in rendered_html[0] or "Summer meeting" not in rendered_html[0].split(utc_time)[0]
