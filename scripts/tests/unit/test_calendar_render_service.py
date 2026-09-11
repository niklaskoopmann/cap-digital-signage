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


def test_seconds_until_midnight() -> None:
    now = datetime.fromisoformat("2026-09-10T23:30:00+00:00")

    assert service.seconds_until_midnight(now, "UTC") == 1800


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
    assert len(calls) == 1