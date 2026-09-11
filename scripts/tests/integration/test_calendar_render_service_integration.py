from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from calendar_render_service import service


class FakeXiboClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object]] = []
        self.layout_state = {"existing-layout": {"tags": ["keep-me"]}}
        self._next_media_id = 1

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
    assert render_calls[0][1] == ["today", "this_week"]
    assert [name for name, _details in client.calls if name == "upload_media"] == [
        "upload_media",
        "upload_media",
    ]
    if not create_layout_per_upload:
        assert client.layout_state == {"existing-layout": {"tags": ["keep-me"]}}
        assert client.calls == [
            ("get_dataset", ("calendar", None)),
            ("get_dataset_data", "calendar-1"),
            ("upload_media", {
                "file_path": images[0].image_path,
                "name": images[0].image_path.name,
                "folder_id": None,
                "tags": ["xibo-sync", "calendar-html", "calendar-image", "calendar-today"],
                "preferred_field": "files",
                "dry_run": False,
            }),
            ("upload_media", {
                "file_path": images[1].image_path,
                "name": images[1].image_path.name,
                "folder_id": None,
                "tags": ["xibo-sync", "calendar-html", "calendar-image", "calendar-this_week"],
                "preferred_field": "files",
                "dry_run": False,
            }),
        ]
    else:
        assert len([name for name, _details in client.calls if name == "create_fullscreen_layout"]) == 2
        assert len([name for name, _details in client.calls if name == "tag_layout"]) == 2
        assert len([name for name, _details in client.calls if name == "publish_layout"]) == 4
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