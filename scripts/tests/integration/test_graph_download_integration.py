"""Offline integration tests for Graph transport and download orchestration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from xibo_sync.calendar_data import load_events
from xibo_sync.config import load_config
from xibo_sync.graph_client import GraphClient
from xibo_sync.graph_download import run_download_phase


class FakeResponse:
    def __init__(self, payload=None, *, content=(), status_code=200):
        self.payload = payload or {}
        self.content = content
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield from self.content


class FakeMsalApp:
    def __init__(self):
        self.calls = []

    def acquire_token_for_client(self, **kwargs):
        self.calls.append(kwargs)
        return {"access_token": "integration-token"}


class FakeSession:
    def __init__(self):
        self.calls = []
        self.page_responses = []
        self.download_responses = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        responses = self.download_responses if kwargs.get("stream") else self.page_responses
        return responses.pop(0)


@pytest.fixture
def cfg(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CMS_BASE_URL", "http://cms.test")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("SHAREPOINT_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("SYNC_MODE", "upload")
    monkeypatch.setenv("CALENDAR_JSON_PATH", str(tmp_path / "calendar"))
    return load_config(sync_mode="upload")


def _pages():
    return [
        FakeResponse({
            "@odata.context": "calendar-context",
            "@odata.nextLink": "https://graph.test/v1.0/calendar-page-2",
            "value": [{"id": "event-1", "subject": "First"}],
        }),
        FakeResponse({"value": [{"id": "event-2", "subject": "Second"}]}),
        FakeResponse({
            "@odata.nextLink": "https://graph.test/v1.0/children-page-2",
            "value": [{
                "id": "media-1",
                "name": "poster.png",
                "file": {},
                "eTag": "etag-1",
                "size": 7,
                "lastModifiedDateTime": "2026-10-06T10:00:00Z",
                "@microsoft.graph.downloadUrl": "https://download.test/poster-token",
            }],
        }),
        FakeResponse({"value": []}),
    ]


def test_real_graph_download_phase_writes_snapshot_media_and_manifest_then_skips(
    cfg, tmp_path: Path
) -> None:
    cfg.graph_tenant_id = "tenant"
    cfg.graph_client_id = "client"
    cfg.graph_client_secret = "client-secret"
    cfg.graph_sharepoint_site_id = "site-id"
    cfg.graph_calendar_user_id = "operator@example.test"
    cfg.graph_base_url = "https://graph.test/v1.0"
    cfg.graph_timeout_seconds = 13
    cfg.media_extensions = (".png",)
    cfg.dry_run = False

    msal_app = FakeMsalApp()
    session = FakeSession()
    session.page_responses.extend(_pages())
    session.download_responses.append(FakeResponse(content=[b"poster", b"!"]))
    graph = GraphClient(
        cfg.graph_tenant_id,
        cfg.graph_client_id,
        cfg.graph_client_secret,
        cfg.graph_base_url,
        cfg.graph_timeout_seconds,
        msal_app=msal_app,
        session=session,
    )
    now = datetime(2026, 10, 6, 12, 30, tzinfo=ZoneInfo("Europe/Berlin"))

    first = run_download_phase(cfg, tmp_path, graph=graph, now=now)

    assert first.errors == []
    snapshot, rows = load_events(cfg.calendar_json_path)
    assert snapshot.name == "office_calendar_events_2026-10-06_12-30-00.json"
    assert [row["subject"] for row in rows] == ["First", "Second"]
    assert (cfg.sharepoint_media_dir / "poster.png").read_bytes() == b"poster!"
    manifest = json.loads(
        (cfg.sharepoint_media_dir / ".sharepoint_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "poster.png": {
            "id": "media-1",
            "eTag": "etag-1",
            "size": 7,
            "lastModifiedDateTime": "2026-10-06T10:00:00Z",
        }
    }
    download_call = next(call for call in session.calls if call[1].get("stream"))
    assert download_call[0] == "https://download.test/poster-token"
    assert download_call[1]["headers"] == {}
    assert all(call[1]["timeout"] == 13 for call in session.calls)

    session.page_responses.extend(_pages())
    second = run_download_phase(cfg, tmp_path, graph=graph, now=now)

    assert second.errors == []
    assert second.media.skipped == 1
    assert len(session.download_responses) == 0
    assert len([call for call in session.calls if call[1].get("stream")]) == 1
    assert msal_app.calls == [
        {"scopes": ["https://graph.microsoft.com/.default"]},
        {"scopes": ["https://graph.microsoft.com/.default"]},
    ]