"""Unit tests for scripts/xibo_sync/wizard.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from xibo_sync.wizard import config_wizard, must_be_sync_mode


def test_wizard_writes_calendar_html_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard must persist all calendar HTML keys to the .env file."""
    env_path = tmp_path / ".env"

    # Patch prompt_edit and prompt_password so the wizard runs non-interactively.
    # Every prompt returns its current default unchanged.
    import xibo_sync.wizard as wizard_module

    def _auto_prompt(key: str, current: str, **_kwargs) -> str:
        return current

    def _auto_password(key: str, current: str, **_kwargs) -> str:
        return current

    monkeypatch.setattr(wizard_module, "prompt_edit", _auto_prompt)
    monkeypatch.setattr(wizard_module, "prompt_password", _auto_password)

    config_wizard(env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "CALENDAR_ENABLE_HTML" in content
    assert "CALENDAR_HTML_VIEWS" in content
    assert "CALENDAR_TEMPLATE_DIR" in content
    # CALENDAR_LAYOUT_NAMES should not be present (removed in refactoring)
    assert "CALENDAR_LAYOUT_NAMES" not in content
    assert "CALENDAR_AUTO_PUBLISH" in content
    assert "CALENDAR_TIMEZONE" in content
    assert "CALENDAR_PACKAGE_RETENTION_DAYS" in content


def test_wizard_persists_graph_and_sync_settings_without_legacy_media_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xibo_sync.wizard as wizard_module

    monkeypatch.setattr(wizard_module, "prompt_edit", lambda key, current, **kwargs: current)
    monkeypatch.setattr(wizard_module, "prompt_password", lambda key, current: current)
    env_path = tmp_path / ".env"

    config_wizard(env_path)

    content = env_path.read_text(encoding="utf-8")
    for key in (
        "SHAREPOINT_MEDIA_DIR", "CALENDAR_IMAGE_DIR", "SYNC_MODE", "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SHAREPOINT_SITE_ID",
        "GRAPH_SHAREPOINT_FOLDER", "GRAPH_CALENDAR_USER_ID", "GRAPH_CALENDAR_DAYS_AHEAD",
        "GRAPH_CALENDAR_KEEP_SNAPSHOTS", "GRAPH_TIMEOUT_SECONDS", "GRAPH_BASE_URL",
    ):
        assert f"{key}=" in content
    assert "LOCAL_MEDIA_DIR=" not in content
    assert "SHAREPOINT_MEDIA_DIR=../media" in content


@pytest.mark.parametrize("mode", ["full", "download", "upload", "FULL"])
def test_must_be_sync_mode_accepts_supported_modes(mode: str) -> None:
    must_be_sync_mode(mode)


def test_must_be_sync_mode_rejects_other_values() -> None:
    with pytest.raises(ValueError, match="full.*download.*upload"):
        must_be_sync_mode("partial")
