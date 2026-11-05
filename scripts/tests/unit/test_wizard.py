"""Unit tests for scripts/xibo_sync/wizard.py."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from xibo_sync.env_io import CREDENTIAL_KEYS, SETTING_KEYS, load_settings
from xibo_sync.wizard import config_wizard, must_be_sync_mode


@pytest.fixture(autouse=True)
def isolated_prompts(monkeypatch):
    import xibo_sync.wizard as wizard_module

    for key in CREDENTIAL_KEYS | SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: current)
    monkeypatch.setattr(wizard_module, "prompt_password", lambda key, current: current)


def test_first_run_enter_uses_defaults_without_creating_files(tmp_path):
    effective = config_wizard(tmp_path / ".env")
    assert effective["CALENDAR_ENABLE_HTML"] == "false"
    assert effective["SHAREPOINT_MEDIA_DIR"] == "../media"
    assert list(tmp_path.iterdir()) == []


def test_changed_fields_route_to_state_and_secrets_and_survive_restart(tmp_path, monkeypatch):
    import xibo_sync.wizard as wizard_module

    env_path = tmp_path / ".env"
    baseline = b"# operator baseline\r\nCALENDAR_TIMEZONE=UTC\r\n"
    env_path.write_bytes(baseline)
    (tmp_path / "sync-state.json").write_text('{"MANAGED_TAG": "keep"}')
    edits = {
        "CALENDAR_ENABLE_HTML": "true", "CALENDAR_TIMEZONE": "Europe/Berlin",
        "CMS_CLIENT_ID": "test-id", "CMS_CLIENT_SECRET": "test-secret",
        "GRAPH_CLIENT_SECRET": "graph-test-secret", "SYNC_MODE": "upload",
    }
    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: edits.get(key, current))
    monkeypatch.setattr(wizard_module, "prompt_password", lambda key, current: edits.get(key, current))

    effective = config_wizard(env_path)
    state = json.loads((tmp_path / "sync-state.json").read_text())
    secrets = json.loads((tmp_path / "secrets.json").read_text())
    assert state == {"MANAGED_TAG": "keep", **{key: value for key, value in edits.items() if key not in CREDENTIAL_KEYS}}
    assert secrets == {key: value for key, value in edits.items() if key in CREDENTIAL_KEYS}
    assert all(effective[key] == value for key, value in edits.items())
    assert env_path.read_bytes() == baseline
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: current)
    monkeypatch.setattr(wizard_module, "prompt_password", lambda key, current: current)
    restarted = config_wizard(env_path)
    assert all(restarted[key] == value for key, value in edits.items())
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_environment_not_copied_and_remains_authoritative_after_edit(tmp_path, monkeypatch):
    import xibo_sync.wizard as wizard_module

    monkeypatch.setenv("CMS_CLIENT_SECRET", "process-secret")
    monkeypatch.setenv("MANAGED_TAG", "process-tag")
    config_wizard(tmp_path / ".env")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: "saved-tag" if key == "MANAGED_TAG" else current)
    effective = config_wizard(tmp_path / ".env")
    assert effective["MANAGED_TAG"] == "process-tag"
    assert json.loads((tmp_path / "sync-state.json").read_text()) == {"MANAGED_TAG": "saved-tag"}
    assert not (tmp_path / "secrets.json").exists()
    monkeypatch.delenv("MANAGED_TAG")
    assert load_settings(tmp_path)[0]["MANAGED_TAG"] == "saved-tag"


@pytest.mark.parametrize("dry_source", ["argument", "baseline", "environment"])
def test_dry_run_cannot_be_disabled_by_wizard_edit(tmp_path, monkeypatch, dry_source):
    import xibo_sync.wizard as wizard_module

    if dry_source == "baseline":
        (tmp_path / ".env").write_text("DRY_RUN=true\n")
    elif dry_source == "environment":
        monkeypatch.setenv("DRY_RUN", "true")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: {"DRY_RUN": "false", "MANAGED_TAG": "preview"}.get(key, current))
    monkeypatch.setattr(wizard_module, "prompt_password", lambda key, current: "preview-secret")
    monkeypatch.setattr("xibo_sync.env_io.atomic_write", lambda *args: pytest.fail("dry-run wrote configuration"))
    config_wizard(tmp_path / ".env", dry_run=dry_source == "argument")
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("mode", ["full", "download", "upload", "FULL"])
def test_must_be_sync_mode_accepts_supported_modes(mode: str) -> None:
    must_be_sync_mode(mode)


def test_must_be_sync_mode_rejects_other_values() -> None:
    with pytest.raises(ValueError, match="full.*download.*upload"):
        must_be_sync_mode("partial")
