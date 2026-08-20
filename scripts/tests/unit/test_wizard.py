"""Unit tests for scripts/xibo_sync/wizard.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from xibo_sync.wizard import config_wizard


def test_wizard_writes_calendar_html_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard must persist all six new calendar HTML keys to the .env file."""
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
    assert "CALENDAR_LAYOUT_NAMES" in content
    assert "CALENDAR_AUTO_PUBLISH" in content
    assert "CALENDAR_TIMEZONE" in content
    assert "CALENDAR_PACKAGE_RETENTION_DAYS" in content
