"""Integration tests for run_calendar_html_upload orchestration in app.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

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

    def upload_html_package(
        self, file_path: Path, tags: list[str], dry_run: bool
    ) -> Optional[dict]:
        self.calls.append(("upload_html_package", (str(file_path), tags, dry_run)))
        return {"mediaId": "media-101"}

    def get_or_create_calendar_layout(
        self, layout_name: str, resolution_id: Optional[int], dry_run: bool
    ) -> Optional[dict]:
        self.calls.append(("get_or_create_calendar_layout", (layout_name, resolution_id, dry_run)))
        return {"layoutId": "layout-201", "layout": layout_name}

    def assign_html_package_to_layout(
        self, layout_id: str, media_id: str, dry_run: bool
    ) -> None:
        self.calls.append(("assign_html_package_to_layout", (layout_id, media_id, dry_run)))

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))

    def assign_layouts_to_displaygroup(
        self, display_group_id: str, layout_ids: list[str], dry_run: bool = False
    ) -> None:
        self.calls.append(("assign_layouts_to_displaygroup", (display_group_id, layout_ids, dry_run)))

    def deploy_calendar_package_to_layout(
        self,
        layout_name: str,
        package_path: Path,
        tags: list[str],
        publish: bool,
        assign_to_display_group_id: Optional[str],
        immediate_show: bool,
        dry_run: bool,
    ) -> Optional[str]:
        self.calls.append(
            (
                "deploy_calendar_package_to_layout",
                {
                    "layout_name": layout_name,
                    "package_path": str(package_path),
                    "tags": tags,
                    "publish": publish,
                    "assign_to_display_group_id": assign_to_display_group_id,
                    "immediate_show": immediate_show,
                    "dry_run": dry_run,
                },
            )
        )
        return "layout-201"


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
    monkeypatch.delenv("DISPLAY_GROUP_ID", raising=False)
    monkeypatch.delenv("CALENDAR_DATASET_CODE", raising=False)
    monkeypatch.delenv("TRIGGER_COLLECTNOW_ON_CHANGES", raising=False)


def test_run_calendar_html_upload_generates_and_deploys_all_views(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """Three packages (one per view) must be generated and each deployed."""
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

    exit_code = app.run_calendar_html_upload(cfg, tmp_path)

    assert exit_code == 0

    client = fake_holder["client"]
    deploy_calls = [c for c in client.calls if c[0] == "deploy_calendar_package_to_layout"]

    assert len(deploy_calls) == 3, (
        f"Expected 3 deploy calls (one per view), got {len(deploy_calls)}"
    )

    deployed_layout_names = [c[1]["layout_name"] for c in deploy_calls]
    assert "Calendar Today" in deployed_layout_names
    assert "Calendar This Week" in deployed_layout_names
    assert "Calendar Next 2 Weeks" in deployed_layout_names

    for call in deploy_calls:
        args = call[1]
        assert "calendar-html" in args["tags"]
        assert args["publish"] is True
        assert args["dry_run"] is False


def test_run_calendar_html_upload_dry_run_skips_deploy(
    monkeypatch: pytest.MonkeyPatch,
    calendar_snapshot_dir: Path,
    tmp_path: Path,
) -> None:
    """In dry-run mode no deploy calls should be made."""
    # Use the bundled template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    
    _setup_env(monkeypatch, calendar_snapshot_dir, template_dir, dry_run=True)
    cfg = load_config()
    cfg.dry_run = True
    cfg.calendar_json_path = calendar_snapshot_dir

    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    exit_code = app.run_calendar_html_upload(cfg, tmp_path)

    assert exit_code == 0

    client = fake_holder["client"]
    deploy_calls = [c for c in client.calls if c[0] == "deploy_calendar_package_to_layout"]
    assert len(deploy_calls) == 0, "dry_run must not call deploy_calendar_package_to_layout"


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
    
    UTC event at 2026-08-25T12:00:00 should display as 14:00 in Berlin summer time (CEST).
    """
    import zipfile
    
    # Create a calendar snapshot with UTC events
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir()
    
    events = [
        _make_event("Summer meeting", "2026-08-25T12:00:00", "2026-08-25T13:00:00"),
    ]
    snapshot = calendar_dir / "office_calendar_events_2026-08-25_10-00-00.json"
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
    
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()

    fake_holder: dict[str, FakeXiboClient] = {}

    def _factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", _factory)

    exit_code = app.run_calendar_html_upload(cfg, packages_dir)

    assert exit_code == 0
    
    # Find the generated .htz package for today
    # Packages are written to <scripts_dir>/calendar_packages/ by default
    htz_files = list((packages_dir / "calendar_packages").glob("calendar_today_*.htz"))
    assert len(htz_files) == 1, f"Expected 1 .htz file, found {len(htz_files)} in {packages_dir / 'calendar_packages'}"
    
    # Extract and read the HTML from the package
    with zipfile.ZipFile(htz_files[0]) as archive:
        html_content = archive.read("index.html").decode("utf-8")
    
    # Verify the HTML contains Berlin-local time (14:00, not UTC 12:00)
    # The event should show 14:00 because CEST is UTC+2
    assert "14:00" in html_content, f"Expected Berlin time 14:00 in HTML, got: {html_content}"
    # Should NOT show the UTC time
    assert "12:00" not in html_content or "Summer meeting" not in html_content.split("12:00")[0] if "12:00" in html_content else True
