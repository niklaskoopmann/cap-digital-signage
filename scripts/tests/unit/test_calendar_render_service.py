from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from calendar_render_service import service


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.collect_now_calls = []

    def get_dataset(self, name, code):
        return {"dataSetId": "dataset-1"}

    def get_dataset_data(self, dataset_id):
        return self.rows

    def collect_now(self, display_group_id, dry_run):
        self.collect_now_calls.append((display_group_id, dry_run))


def make_config(tmp_path: Path):
    return SimpleNamespace(
        cms_base_url="http://cms",
        cms_verify_tls=False,
        cms_timeout=10,
        cms_client_id="id",
        cms_client_secret="secret",
        calendar_dataset_name="calendar",
        calendar_dataset_code=None,
        calendar_html_views=("today",),
        calendar_template_dir=tmp_path,
        calendar_timezone="UTC",
        calendar_event_retention_days=30,
        output_dir=tmp_path / "output",
        managed_tag="xibo-sync",
        managed_folder_id=None,
        create_layout_per_upload=False,
        assign_layout_on_change=False,
        immediate_show_on_change=False,
        display_group_id=None,
        trigger_collectnow_on_changes=False,
        xibo_upload_field="files",
        cleanup_old_view_uploads=True,
        dry_run=False,
    )


def test_run_once_filters_dataset_rows_and_uploads_generated_images(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.display_group_id = "2"
    cfg.trigger_collectnow_on_changes = True
    client = FakeClient([
        {"eventIdentifier": "recent", "endDateTime": "2026-09-09T12:00:00", "endTimeZone": "UTC"},
        {"eventIdentifier": "old", "endDateTime": "2026-08-01T12:00:00", "endTimeZone": "UTC"},
    ])
    image = SimpleNamespace(view_type="today", image_path=tmp_path / "calendar_today.png")
    uploaded = []
    monkeypatch.setattr(service, "generate_calendar_images", lambda *args, **kwargs: [image])
    monkeypatch.setattr(service, "_upload_media_with_optional_layout", lambda *args, **kwargs: uploaded.append(kwargs))

    result = service.run_once(
        cfg,
        client=client,
        now=datetime.fromisoformat("2026-09-10T12:00:00+00:00"),
    )

    assert result == {"events": 1, "images": 1, "uploads": 1}
    assert uploaded[0]["file_path"] == image.image_path
    assert client.collect_now_calls == [("2", False)]


def test_run_once_skips_missing_dataset(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = FakeClient([])
    monkeypatch.setattr(client, "get_dataset", lambda name, code: None)

    assert service.run_once(cfg, client=client) == {"events": 0, "images": 0, "uploads": 0}


def test_cleanup_previous_view_uploads_cleans_layout_and_media_after_schedule_clone(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.create_layout_per_upload = True
    cfg.cleanup_old_view_uploads = True
    cfg.display_group_id = "group-9"
    old_media_id = "media-old"
    current_media_id = "media-current"
    old_layout = {"layoutId": "layout-old", "campaignId": "campaign-old", "tags": ["xibo-sync-media:media-old", "xibo-sync", "calendar-today"]}
    new_layout = {"layoutId": "layout-new", "campaignId": "campaign-new", "tags": ["xibo-sync-media:media-current", "xibo-sync", "calendar-today"]}
    calls = []

    class StubXibo:
        def list_library_by_tags(self, tags, folder_id):
            calls.append(("list_library_by_tags", tags, folder_id))
            return [{"mediaId": old_media_id, "tags": ["xibo-sync", "calendar-today"]}]

        def list_layouts_by_ownership_tag(self, ownership_tag):
            calls.append(("list_layouts_by_ownership_tag", ownership_tag))
            return [old_layout] if ownership_tag == "xibo-sync-media:media-old" else [new_layout]

        def clone_schedule_events_to_campaign(self, old_campaign_id, new_campaign_id, dry_run=False):
            calls.append(("clone_schedule_events_to_campaign", old_campaign_id, new_campaign_id, dry_run))
            return 1

        def cleanup_layout_for_media(self, layout, media_id, ownership_tag, display_group_ids=None, dry_run=False):
            calls.append(("cleanup_layout_for_media", media_id, ownership_tag, display_group_ids, dry_run))

        def delete_media(self, media_id, dry_run=False):
            calls.append(("delete_media", media_id, dry_run))

    service.cleanup_previous_view_uploads(StubXibo(), cfg, "today", current_media_id)

    assert ("clone_schedule_events_to_campaign", "campaign-old", "campaign-new", False) in calls
    assert any(call[0] == "cleanup_layout_for_media" and call[1] == old_media_id for call in calls)
    assert any(call[0] == "delete_media" and call[1] == old_media_id for call in calls)


def test_cleanup_previous_view_uploads_handles_media_only_mode_and_flag_off(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.create_layout_per_upload = False
    cfg.cleanup_old_view_uploads = True

    calls = []

    class StubXibo:
        def list_library_by_tags(self, tags, folder_id):
            return [{"mediaId": "media-old", "tags": ["xibo-sync", "calendar-today"]}]

        def delete_media(self, media_id, dry_run=False):
            calls.append(("delete_media", media_id, dry_run))

    service.cleanup_previous_view_uploads(StubXibo(), cfg, "today", "media-current")
    assert calls == [("delete_media", "media-old", False)]

    cfg.cleanup_old_view_uploads = False
    calls.clear()
    service.cleanup_previous_view_uploads(StubXibo(), cfg, "today", "media-current")
    assert calls == []


def test_cleanup_previous_view_uploads_ignores_failed_old_items(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.create_layout_per_upload = True
    cfg.cleanup_old_view_uploads = True

    class StubXibo:
        def list_library_by_tags(self, tags, folder_id):
            return [
                {"mediaId": "media-bad", "tags": ["xibo-sync", "calendar-today"]},
                {"mediaId": "media-good", "tags": ["xibo-sync", "calendar-today"]},
            ]

        def list_layouts_by_ownership_tag(self, ownership_tag):
            if ownership_tag == "xibo-sync-media:media-bad":
                raise RuntimeError("bad layout")
            return [{"layoutId": "layout-good", "campaignId": "campaign-good", "tags": ["xibo-sync-media:media-good"]}]

        def clone_schedule_events_to_campaign(self, old_campaign_id, new_campaign_id, dry_run=False):
            return 1

        def cleanup_layout_for_media(self, layout, media_id, ownership_tag, display_group_ids=None, dry_run=False):
            if media_id == "media-good":
                return None
            raise RuntimeError("cleanup failed")

        def delete_media(self, media_id, dry_run=False):
            pass

    service.cleanup_previous_view_uploads(StubXibo(), cfg, "today", "media-current")


def test_seconds_until_midnight() -> None:
    now = datetime.fromisoformat("2026-09-10T23:30:00+00:00")

    assert service.seconds_until_midnight(now, "UTC") == 1800


def test_setup_logging_configures_root_logger_level(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        service.logging,
        "basicConfig",
        lambda **kwargs: captured.update(kwargs),
    )

    service.setup_logging("DEBUG")

    assert captured["level"] == service.logging.DEBUG


def test_run_forever_uses_configured_cadence_after_first_midnight(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.schedule_seconds = 900
    sleeps = []
    calls = []

    def stop_after_second_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise RuntimeError("stop test scheduler")

    monkeypatch.setattr(service, "seconds_until_midnight", lambda now, timezone: 1800)

    try:
        service.run_forever(
            cfg,
            job=lambda config, now=None: calls.append(now),
            now_fn=lambda: datetime.fromisoformat("2026-09-10T23:30:00+00:00"),
            sleep_fn=stop_after_second_sleep,
        )
    except RuntimeError as error:
        assert str(error) == "stop test scheduler"

    assert sleeps == [1800, 900]
    assert len(calls) == 2


def test_run_forever_runs_immediately_on_startup_before_first_sleep(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    calls = []

    def stop_after_first_sleep(seconds):
        raise RuntimeError("stop test scheduler")

    monkeypatch.setattr(service, "seconds_until_midnight", lambda now, timezone: 1800)

    try:
        service.run_forever(
            cfg,
            job=lambda config, now=None: calls.append(now),
            now_fn=lambda: datetime.fromisoformat("2026-09-10T23:30:00+00:00"),
            sleep_fn=stop_after_first_sleep,
        )
    except RuntimeError as error:
        assert str(error) == "stop test scheduler"

    assert len(calls) == 1