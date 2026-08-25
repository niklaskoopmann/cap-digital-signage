"""Integration test: config + calendar_data + a mocked XiboClient cooperating via app.py."""

import json
from pathlib import Path
from typing import Any

import pytest

from xibo_sync import app
from xibo_sync.config import load_config


class FakeXiboClient:
    """Records calls instead of making real HTTP requests to Xibo."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        self.calls: list[tuple[str, Any]] = []

    def health_check(self) -> None:
        self.calls.append(("health_check", None))

    def get_dataset(self, name: str, code: str | None = None) -> dict | None:
        self.calls.append(("get_dataset", (name, code)))
        return None  # simulate: DataSet does not exist yet

    def create_dataset(self, name: str, code: str | None = None) -> dict:
        self.calls.append(("create_dataset", (name, code)))
        return {"dataSetId": "10"}

    def list_dataset_columns(self, dataset_id: str) -> list[dict]:
        self.calls.append(("list_dataset_columns", dataset_id))
        return []  # simulate: no columns exist yet, all must be created

    def create_dataset_column(self, dataset_id: str, heading: str, column_order: int) -> dict:
        self.calls.append(("create_dataset_column", (dataset_id, heading, column_order)))
        return {"dataSetColumnId": f"col-{column_order}"}

    def import_dataset_csv(self, dataset_id: str, csv_content: bytes, column_ids: list[str]) -> None:
        self.calls.append(("import_dataset_csv", (dataset_id, csv_content, column_ids)))


@pytest.fixture
def calendar_snapshot_dir(tmp_path: Path) -> Path:
    snapshot = tmp_path / "office_calendar_events_2026-08-19_13-10-01.json"
    snapshot.write_text(
        json.dumps({"value": [{"id": "event-1", "subject": "Planning"}]}),
        encoding="utf-8",
    )
    return tmp_path


def _load_test_config(monkeypatch: pytest.MonkeyPatch, calendar_dir: Path):
    monkeypatch.setenv("CMS_BASE_URL", "http://192.168.1.1")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", "../media")
    monkeypatch.setenv("CALENDAR_JSON_PATH", str(calendar_dir))
    monkeypatch.setenv("CALENDAR_DATASET_NAME", "office_calendar_events")
    monkeypatch.setenv("TRIGGER_COLLECTNOW_ON_CHANGES", "false")
    monkeypatch.delenv("DISPLAY_GROUP_ID", raising=False)
    monkeypatch.delenv("CALENDAR_DATASET_CODE", raising=False)
    return load_config()


def test_run_calendar_upload_creates_missing_dataset_and_columns(
    monkeypatch: pytest.MonkeyPatch, calendar_snapshot_dir: Path
) -> None:
    cfg = _load_test_config(monkeypatch, calendar_snapshot_dir)
    fake_client_holder: dict[str, FakeXiboClient] = {}

    def fake_client_factory(base_url: str, verify_tls: bool, timeout: int) -> FakeXiboClient:
        client = FakeXiboClient(base_url, verify_tls, timeout)
        fake_client_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", fake_client_factory)

    exit_code = app.run_calendar_upload(cfg, calendar_snapshot_dir)

    assert exit_code == 0
    fake_client = fake_client_holder["client"]
    call_names = [name for name, _ in fake_client.calls]

    assert "health_check" in call_names
    assert "create_dataset" in call_names  # DataSet did not exist, so it must be created
    assert call_names.count("create_dataset_column") > 0  # no columns existed yet

    import_call = next(c for c in fake_client.calls if c[0] == "import_dataset_csv")
    dataset_id, csv_content, column_ids = import_call[1]
    assert dataset_id == "10"
    assert b"event-1" in csv_content
    assert len(column_ids) == len(column_ids)  # one column id per configured calendar column


def test_run_calendar_upload_reuses_existing_dataset_and_columns(
    monkeypatch: pytest.MonkeyPatch, calendar_snapshot_dir: Path
) -> None:
    cfg = _load_test_config(monkeypatch, calendar_snapshot_dir)

    class ReusingFakeXiboClient(FakeXiboClient):
        def get_dataset(self, name: str, code: str | None = None) -> dict | None:
            self.calls.append(("get_dataset", (name, code)))
            return {"dataSetId": "existing-10"}

        def list_dataset_columns(self, dataset_id: str) -> list[dict]:
            self.calls.append(("list_dataset_columns", dataset_id))
            from xibo_sync.calendar_data import CALENDAR_COLUMNS

            return [
                {"heading": heading, "dataSetColumnId": f"col-{i}"}
                for i, heading in enumerate(CALENDAR_COLUMNS)
            ]

    fake_client_holder: dict[str, ReusingFakeXiboClient] = {}

    def fake_client_factory(base_url: str, verify_tls: bool, timeout: int) -> ReusingFakeXiboClient:
        client = ReusingFakeXiboClient(base_url, verify_tls, timeout)
        fake_client_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", fake_client_factory)

    exit_code = app.run_calendar_upload(cfg, calendar_snapshot_dir)

    assert exit_code == 0
    fake_client = fake_client_holder["client"]
    call_names = [name for name, _ in fake_client.calls]

    assert "create_dataset" not in call_names  # DataSet already existed
    assert "create_dataset_column" not in call_names  # all columns already existed
