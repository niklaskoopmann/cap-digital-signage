"""Unit tests for the terminal settings summary."""

from io import StringIO

from rich.console import Console

import xibo_sync.ui as ui
import pytest
from xibo_sync.env_io import CREDENTIAL_KEYS


def test_settings_table_masks_all_credentials_with_source_labels(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=output, width=180, color_system=None))
    values = {key: f"canary-{key}" for key in CREDENTIAL_KEYS}
    ui.ui_settings_table(values, {key: "secrets.json" for key in values})
    rendered = output.getvalue()
    assert all(value not in rendered for value in values.values())
    assert rendered.count("******") == len(CREDENTIAL_KEYS)
    assert rendered.count("secrets.json") == len(CREDENTIAL_KEYS)


@pytest.mark.parametrize("key", sorted(CREDENTIAL_KEYS))
def test_credential_prompt_never_exposes_current_value(key, monkeypatch, tmp_path):
    from xibo_sync.wizard import config_wizard

    output = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=output, width=180, color_system=None))
    monkeypatch.setenv(key, "private-canary")
    defaults = []

    def ask(*args, **kwargs):
        defaults.append(kwargs.get("default", ""))
        return kwargs.get("default", "")

    monkeypatch.setattr(ui.Prompt, "ask", ask)
    monkeypatch.setattr(ui.getpass, "getpass", lambda *args: "")
    assert config_wizard(tmp_path / ".env")[key] == "private-canary"
    assert "private-canary" not in output.getvalue()
    assert "private-canary" not in defaults


def test_settings_table_masks_both_secrets_and_shows_mode_paths(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=output, width=120, force_terminal=True))

    ui.ui_settings_table({
        "CMS_CLIENT_SECRET": "cms-secret-value",
        "GRAPH_CLIENT_SECRET": "graph-secret-value",
        "SHAREPOINT_MEDIA_DIR": "../media",
        "SYNC_MODE": "download",
    })

    rendered = output.getvalue()
    assert "cms-secret-value" not in rendered
    assert "graph-secret-value" not in rendered
    assert rendered.count("******") == 2
    assert "SHAREPOINT_MEDIA_DIR" in rendered
    assert "SYNC_MODE" in rendered