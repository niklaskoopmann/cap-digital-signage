from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import os

import pytest
import yaml

from calendar_render_service import service


class FakeXiboClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object]] = []
        self.layout_state = {"existing-layout": {"tags": ["keep-me"]}}
        self._next_media_id = 1

    def authenticate_oauth(self, client_id: str, client_secret: str) -> None:
        self.calls.append(("authenticate_oauth", (client_id, client_secret)))

    def get_dataset(self, name: str, code: str | None = None) -> dict[str, str]:
        self.calls.append(("get_dataset", (name, code)))
        return {"dataSetId": "calendar-1"}

    def get_dataset_data(self, dataset_id: str) -> list[dict[str, str]]:
        self.calls.append(("get_dataset_data", dataset_id))
        return self.rows

    def upload_media(self, **kwargs: object) -> dict[str, object]:
        media_id = f"media-{self._next_media_id}"
        self._next_media_id += 1
        self.calls.append(("upload_media", kwargs))
        return {"mediaId": media_id, "name": kwargs["name"]}

    def create_fullscreen_layout(self, media: dict[str, object], dry_run: bool) -> dict[str, str]:
        layout_id = f"draft-{media['mediaId']}"
        self.calls.append(("create_fullscreen_layout", (media, dry_run)))
        self.layout_state[layout_id] = {"tags": []}
        return {"layoutId": layout_id, "layout": f"{media['name']} fullscreen"}

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))

    def get_layout_by_name(self, name: str) -> dict[str, str]:
        layout_id = f"published-{name}"
        self.calls.append(("get_layout_by_name", name))
        self.layout_state.setdefault(layout_id, {"tags": []})
        return {"layoutId": layout_id, "layout": name}

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))
        self.layout_state[layout_id]["tags"] = tags

    def list_library_by_tags(self, tags: list[str], folder_id: str | None = None) -> list[dict[str, object]]:
        self.calls.append(("list_library_by_tags", (tags, folder_id)))
        return []

    def list_layouts_by_ownership_tag(self, ownership_tag: str) -> list[dict[str, object]]:
        self.calls.append(("list_layouts_by_ownership_tag", ownership_tag))
        return []

    def clone_schedule_events_to_campaign(self, old_campaign_id: str, new_campaign_id: str, dry_run: bool = False) -> int:
        self.calls.append(("clone_schedule_events_to_campaign", (old_campaign_id, new_campaign_id, dry_run)))
        return 0

    def cleanup_layout_for_media(
        self,
        layout: dict[str, object],
        media_id: str,
        ownership_tag: str,
        display_group_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.calls.append(("cleanup_layout_for_media", (layout, media_id, ownership_tag, display_group_ids, dry_run)))

    def delete_media(self, media_id: str, dry_run: bool = False) -> None:
        self.calls.append(("delete_media", (media_id, dry_run)))


def test_calendar_render_compose_mounts_existing_private_files_read_only() -> None:
    compose_path = (
        Path(__file__).resolve().parents[3]
        / "xibo"
        / "xibo-docker-4.4.2"
        / "docker-compose.yml"
    )
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    service_config = compose["services"]["calendar-render-service"]
    mounts = {mount["target"]: mount for mount in service_config["volumes"]}

    assert set(mounts) == {
        "/app/calendar_render_service/.env",
        "/app/calendar_render_service/secrets.json",
    }
    assert all(mount["type"] == "bind" for mount in mounts.values())
    assert all(mount["read_only"] is True for mount in mounts.values())
    assert all(mount["bind"]["create_host_path"] is False for mount in mounts.values())


def make_config(tmp_path: Path, create_layout_per_upload: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        cms_base_url="http://cms",
        cms_verify_tls=False,
        cms_timeout=10,
        cms_client_id="service-id",
        cms_client_secret="service-secret",
        calendar_dataset_name="calendar",
        calendar_dataset_code=None,
        calendar_html_views=("today", "this_week"),
        calendar_template_dir=tmp_path,
        calendar_timezone="UTC",
        calendar_event_retention_days=30,
        output_dir=tmp_path / "output",
        managed_tag="xibo-sync",
        managed_folder_id=None,
        create_layout_per_upload=create_layout_per_upload,
        assign_layout_on_change=False,
        immediate_show_on_change=False,
        display_group_id=None,
        trigger_collectnow_on_changes=False,
        xibo_upload_field="files",
        cleanup_old_view_uploads=True,
        log_level="INFO",
        dry_run=False,
    )


@pytest.mark.parametrize("create_layout_per_upload", [False, True])
def test_render_service_fetches_filters_renders_and_uploads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    create_layout_per_upload: bool,
) -> None:
    client = FakeXiboClient([
        {
            "eventIdentifier": "recent",
            "subject": "Keep",
            "endDateTime": "2026-09-09T12:00:00",
            "endTimeZone": "UTC",
            "location": "Conference Room",
            "organizer": "Alex Organizer",
        },
        {
            "eventIdentifier": "old",
            "subject": "Drop",
            "endDateTime": "2026-08-01T12:00:00",
            "endTimeZone": "UTC",
        },
    ])
    cfg = make_config(tmp_path, create_layout_per_upload)
    images = [
        SimpleNamespace(view_type="today", image_path=tmp_path / "today.png"),
        SimpleNamespace(view_type="this_week", image_path=tmp_path / "this_week.png"),
    ]
    render_calls: list[tuple[list[dict[str, str]], list[str]]] = []

    def fake_render(events, views, *args, **kwargs):
        render_calls.append((events, views))
        return images

    monkeypatch.setattr(service, "generate_calendar_images", fake_render)

    result = service.run_once(
        cfg,
        client=client,
        now=datetime.fromisoformat("2026-09-10T12:00:00+00:00"),
    )

    assert result == {"events": 1, "images": 2, "uploads": 2}
    assert [event["id"] for event in render_calls[0][0]] == ["recent"]
    assert render_calls[0][0][0]["location"] == "Conference Room"
    assert render_calls[0][0][0]["organizer"] == "Alex Organizer"
    assert render_calls[0][1] == ["today", "this_week"]
    assert [name for name, _details in client.calls if name == "upload_media"] == [
        "upload_media",
        "upload_media",
    ]
    if not create_layout_per_upload:
        assert client.layout_state == {"existing-layout": {"tags": ["keep-me"]}}
        assert [name for name, _details in client.calls if name == "upload_media"] == [
            "upload_media",
            "upload_media",
        ]
        assert [details for name, details in client.calls if name == "list_library_by_tags"] == [
            (["xibo-sync", "calendar-today"], None),
            (["xibo-sync", "calendar-this_week"], None),
        ]
    else:
        assert len([name for name, _details in client.calls if name == "create_fullscreen_layout"]) == 2
        assert len([name for name, _details in client.calls if name == "tag_layout"]) == 2
        assert len([name for name, _details in client.calls if name == "publish_layout"]) == 4
        assert [details for name, details in client.calls if name == "list_library_by_tags"] == [
            (["xibo-sync", "calendar-today"], None),
            (["xibo-sync", "calendar-this_week"], None),
        ]
        assert len(client.layout_state) == 5


def test_render_service_skips_empty_dataset_without_render_or_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeXiboClient([])
    cfg = make_config(tmp_path)
    rendered = []
    uploaded = []
    monkeypatch.setattr(service, "generate_calendar_images", lambda *args, **kwargs: rendered.append(args))
    monkeypatch.setattr(
        service,
        "_upload_media_with_optional_layout",
        lambda *args, **kwargs: uploaded.append(args),
    )

    result = service.run_once(
        cfg,
        client=client,
        now=datetime.fromisoformat("2026-09-10T12:00:00+00:00"),
    )

    assert result == {"events": 0, "images": 0, "uploads": 0}
    assert rendered == []
    assert uploaded == []


def test_service_main_runs_one_complete_cycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = FakeXiboClient([
        {
            "eventIdentifier": "one-event",
            "subject": "One event",
            "endDateTime": "2026-09-09T12:00:00",
            "endTimeZone": "UTC",
        }
    ])
    cfg = make_config(tmp_path)
    image = SimpleNamespace(view_type="today", image_path=tmp_path / "today.png")
    uploads = []
    monkeypatch.setattr(service, "load_config", lambda: cfg)
    monkeypatch.setattr(service, "setup_logging", lambda level: None)
    monkeypatch.setattr(service, "XiboClient", lambda *args: client)
    monkeypatch.setattr(service, "generate_calendar_images", lambda *args, **kwargs: [image])
    monkeypatch.setattr(service, "_upload_media_with_optional_layout", lambda *args, **kwargs: uploads.append(kwargs) or {"mediaId": "media-1"})
    original_run_once = service.run_once
    monkeypatch.setattr(service, "run_once", lambda config: original_run_once(config, now=datetime.fromisoformat("2026-09-10T12:00:00+00:00")))

    assert service.main() == 0
    assert [name for name, _details in client.calls].count("get_dataset") == 1
    assert len(uploads) == 1


@pytest.mark.parametrize("run_on_start", [True, False])
def test_scheduler_and_independent_jobs_reload_dedicated_service_files(monkeypatch, tmp_path, run_on_start):
    from calendar_render_service import config as service_config, scheduler
    from xibo_sync.env_io import load_settings
    from xibo_sync.config import load_config

    service_dir = tmp_path / "service"
    sync_dir = tmp_path / "sync"
    service_dir.mkdir()
    sync_dir.mkdir()
    (service_dir / ".env").write_text(f"CMS_BASE_URL=http://service\nCALENDAR_RENDER_CRON=*/15 * * * *\nCALENDAR_RENDER_RUN_ON_START={str(run_on_start).lower()}\n")
    secrets_path = service_dir / "secrets.json"
    secrets_path.write_text('{"CMS_CLIENT_ID":"service-id","CMS_CLIENT_SECRET":"service-secret"}')
    (sync_dir / ".env").write_text("CMS_BASE_URL=http://sync\nSHAREPOINT_MEDIA_DIR=media\nSYNC_MODE=upload\n")
    (sync_dir / "secrets.json").write_text('{"CMS_CLIENT_ID":"sync-id","CMS_CLIENT_SECRET":"sync-secret"}')
    sync_cfg = load_config(values=load_settings(sync_dir)[0])
    assert (sync_cfg.cms_client_id, sync_cfg.cms_client_secret) == ("sync-id", "sync-secret")
    monkeypatch.setattr(service_config, "__file__", str(service_dir / "config.py"))
    monkeypatch.chdir(sync_dir)
    monkeypatch.setattr(service, "setup_logging", lambda *args: None)
    monkeypatch.setattr(scheduler, "setup_logging", lambda *args: None)
    cycles = []
    monkeypatch.setattr(service, "run_once", lambda cfg: cycles.append(cfg) or {})
    cron_path = tmp_path / "service.cron"
    original_write_crontab = scheduler.write_crontab
    monkeypatch.setattr(scheduler, "write_crontab", lambda cfg: original_write_crontab(cfg, cron_path))
    commands = []

    def startup(command, check):
        commands.append(command)
        return SimpleNamespace(returncode=service.main())

    monkeypatch.setattr(scheduler.subprocess, "run", startup)
    exec_calls = []
    monkeypatch.setattr(scheduler.os, "execv", lambda *args: exec_calls.append(args))
    before_environment = dict(os.environ)
    baseline = (service_dir / ".env").read_bytes()
    scheduler.main()
    assert len(cycles) == int(run_on_start)
    assert commands == ([list(scheduler.JOB_COMMAND)] if run_on_start else [])
    assert cron_path.read_text().startswith("*/15 * * * * ")
    assert exec_calls[0][0] == scheduler.SUPERCRONIC_COMMAND
    secrets_path.write_text('{"CMS_CLIENT_ID":"rotated-id","CMS_CLIENT_SECRET":"rotated-secret"}')
    assert service.main() == 0
    assert (cycles[-1].cms_client_id, cycles[-1].cms_client_secret) == ("rotated-id", "rotated-secret")
    assert cycles[-1].cms_base_url == "http://service"
    assert (service_dir / ".env").read_bytes() == baseline
    assert json.loads(secrets_path.read_text())["CMS_CLIENT_ID"] == "rotated-id"
    assert not (service_dir / "sync-state.json").exists()
    assert dict(os.environ) == before_environment


@pytest.mark.parametrize("failure", ["missing_baseline", "missing_secrets", "invalid_secrets", "legacy_credentials", "misplaced_graph_credentials"])
@pytest.mark.parametrize("entrypoint", ["scheduler", "one_shot"])
def test_invalid_service_files_stop_startup_before_jobs_or_network(monkeypatch, tmp_path, failure, entrypoint):
    from calendar_render_service import config as service_config, scheduler

    baseline = tmp_path / ".env"
    secrets = tmp_path / "secrets.json"
    baseline.write_text("CMS_BASE_URL=http://service\n")
    secrets.write_text('{"CMS_CLIENT_ID":"service-id","CMS_CLIENT_SECRET":"private-canary"}')
    if failure == "missing_baseline":
        baseline.unlink()
    elif failure == "missing_secrets":
        secrets.unlink()
    elif failure == "invalid_secrets":
        secrets.write_text('{broken-private-canary')
    elif failure == "legacy_credentials":
        baseline.write_text("CMS_BASE_URL=http://service\nCMS_CLIENT_SECRET=private-canary\n")
    else:
        secrets.write_text('{"GRAPH_CLIENT_SECRET":"private-canary"}')
    monkeypatch.setattr(service_config, "__file__", str(tmp_path / "config.py"))
    calls = []
    monkeypatch.setattr(service, "run_once", lambda *args: calls.append("cycle"))
    monkeypatch.setattr(scheduler, "write_crontab", lambda *args: calls.append("cron"))
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: calls.append("subprocess"))
    monkeypatch.setattr(scheduler.os, "execv", lambda *args: calls.append("exec"))
    with pytest.raises(ValueError) as error:
        (scheduler.main if entrypoint == "scheduler" else service.main)()
    assert "private-canary" not in str(error.value)
    assert calls == []