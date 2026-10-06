"""Unit tests for the terminal settings summary."""

from io import StringIO

from rich.console import Console

import xibo_sync.ui as ui


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