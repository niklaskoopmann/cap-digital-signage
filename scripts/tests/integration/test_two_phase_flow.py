"""Integration tests for two-phase download and Xibo upload orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

import xibo_sync.app as app
import xibo_sync.graph_download as graph_download


class FakeXibo:
    def __init__(self, events: list[str], failures: int = 0):
        self.events = events
        self.failures = failures
        events.append("xibo_construct")

    def authenticate_oauth(self, client_id, client_secret):
        self.events.append("authenticate")

    def health_check(self):
        self.events.append("health_check")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("offline")


def configure_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    env_mode: str = "full",
    cli_args: list[str] | None = None,
    phase_errors: list[str] | None = None,
    health_failures: int = 0,
    answers: list[bool] | None = None,
):
    events: list[str] = []
    prompts: list[str] = []
    stored_env = {"SYNC_MODE": env_mode}
    for key, value in {
        "CMS_BASE_URL": "http://cms.test",
        "AUTH_MODE": "none",
        "SHAREPOINT_MEDIA_DIR": str(tmp_path / "media"),
        "CALENDAR_IMAGE_DIR": str(tmp_path / "calendar-images"),
        "CALENDAR_JSON_PATH": str(tmp_path / "calendar"),
        "SYNC_MODE": env_mode,
        "GRAPH_TENANT_ID": "tenant",
        "GRAPH_CLIENT_ID": "client",
        "GRAPH_CLIENT_SECRET": "secret",
        "GRAPH_SHAREPOINT_SITE_ID": "site",
        "GRAPH_CALENDAR_USER_ID": "user@example.test",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(app, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "read_env_file", lambda _path: dict(stored_env))

    def save_env(_path, values):
        events.append("save_delete_choice")
        stored_env.update(values)

    monkeypatch.setattr(app, "write_env_file", save_env)
    monkeypatch.setattr(app, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(app, "ui_settings_table", lambda *_args: None)
    monkeypatch.setattr(app, "ui_header", lambda *_args: None)
    monkeypatch.setattr(app, "ui_info", lambda *_args: None)
    monkeypatch.setattr(app, "ui_ok", lambda *_args: None)
    monkeypatch.setattr(app, "ui_warn", lambda *_args: None)
    monkeypatch.setattr(app, "ui_error", lambda *_args: None)
    monkeypatch.setattr(app.console, "print", lambda *_args, **_kwargs: None)

    remaining_answers = list(answers or [])

    def confirm(question, default=False):
        prompts.append(question)
        return remaining_answers.pop(0) if remaining_answers else default

    monkeypatch.setattr(app.Confirm, "ask", confirm)
    failures_remaining = [health_failures]

    def make_xibo(*_args):
        failures = failures_remaining[0]
        failures_remaining[0] = max(0, failures_remaining[0] - 1)
        return FakeXibo(events, failures=failures)

    monkeypatch.setattr(app, "XiboClient", make_xibo)

    def download_phase(cfg, _scripts_dir):
        events.append("phase1")
        events.append(f"dry_run={cfg.dry_run}")
        return graph_download.DownloadPhaseResult(errors=list(phase_errors or []))

    monkeypatch.setattr(graph_download, "run_download_phase", download_phase)

    def media_sync(_cfg, run_delete, *, xibo=None):
        events.append("media_sync")
        events.append(f"delete={run_delete}")
        events.append(f"verified_client={xibo is not None}")
        return 0

    monkeypatch.setattr(app, "run_media_sync", media_sync)

    def calendar_upload(*_args, **_kwargs):
        events.append("calendar_upload")
        return 0

    monkeypatch.setattr(app, "run_calendar_upload", calendar_upload)
    monkeypatch.setattr(
        app,
        "run_calendar_html_upload",
        lambda *_args, **_kwargs: events.append("calendar_html") or 0,
    )
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", *(cli_args or ["--yes"])])
    return events, prompts, stored_env


def test_full_mode_orders_download_hotspot_and_upload(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, answers=[True]
    )

    assert app.main() == 0

    assert events == [
        "save_delete_choice", "phase1", "dry_run=False", "xibo_construct",
        "health_check", "media_sync", "delete=False", "verified_client=True",
        "calendar_upload",
    ]
    assert prompts == ["Connected to the Xibo hotspot?"]


def test_interactive_full_mode_orders_delete_decision_before_both_phases(monkeypatch, tmp_path):
    events, prompts, stored_env = configure_main(
        monkeypatch,
        tmp_path,
        cli_args=["--mode", "full"],
        answers=[False, True, True],
    )
    confirm = app.Confirm.ask

    def record_confirm(question, default=False):
        events.append(f"prompt:{question}")
        return confirm(question, default)

    monkeypatch.setattr(app.Confirm, "ask", record_confirm)

    assert app.main() == 0

    assert events == [
        "prompt:[bold]Do you want to override/edit configuration values now?[/bold]",
        "prompt:Delete media from Xibo that is NOT present locally?",
        "save_delete_choice",
        "phase1",
        "dry_run=False",
        "prompt:Connected to the Xibo hotspot?",
        "xibo_construct",
        "health_check",
        "media_sync",
        "delete=True",
        "verified_client=True",
        "calendar_upload",
    ]
    assert prompts == [
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        "Delete media from Xibo that is NOT present locally?",
        "Connected to the Xibo hotspot?",
    ]
    assert stored_env["DELETE_REMOTE_NOT_LOCAL"] == "true"


def test_hotspot_health_check_retries_until_reachable(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, health_failures=1, answers=[True, True]
    )

    assert app.main() == 0

    assert prompts == ["Connected to the Xibo hotspot?"] * 2
    assert events.count("xibo_construct") == 2
    assert events.count("health_check") == 2
    assert "media_sync" in events


def test_hotspot_decline_aborts_before_phase_two(monkeypatch, tmp_path):
    events, _prompts, _stored_env = configure_main(monkeypatch, tmp_path, answers=[False])

    assert app.main() == 2

    assert "phase1" in events
    assert "media_sync" not in events
    assert "calendar_upload" not in events


def test_full_mode_phase_one_error_with_yes_aborts_without_xibo(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, phase_errors=["Graph unavailable"]
    )

    assert app.main() == 2

    assert events[:3] == ["save_delete_choice", "phase1", "dry_run=False"]
    assert "xibo_construct" not in events
    assert prompts == []


def test_interactive_phase_one_error_can_continue(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch,
        tmp_path,
        cli_args=["--no-delete"],
        phase_errors=["Graph unavailable"],
        answers=[False, True, True],
    )

    assert app.main() == 0

    assert prompts == [
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        "Continue with existing local data?",
        "Connected to the Xibo hotspot?",
    ]
    assert "media_sync" in events


def test_interactive_phase_one_error_can_abort(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch,
        tmp_path,
        cli_args=["--no-delete"],
        phase_errors=["Graph unavailable"],
        answers=[False, False],
    )

    assert app.main() == 2

    assert prompts == [
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        "Continue with existing local data?",
    ]
    assert "xibo_construct" not in events
    assert "media_sync" not in events


def test_download_mode_never_prompts_for_hotspot_or_writes_delete_choice(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, env_mode="download"
    )

    assert app.main() == 0

    assert events == ["phase1", "dry_run=False"]
    assert prompts == []


def test_cli_download_mode_overrides_full_environment(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch,
        tmp_path,
        env_mode="full",
        cli_args=["--yes", "--mode", "download"],
    )

    assert app.main() == 0

    assert events == ["phase1", "dry_run=False"]
    assert prompts == []


def test_download_mode_dry_run_reaches_phase_one(monkeypatch, tmp_path):
    events, _prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, env_mode="download", cli_args=["--yes", "--dry-run"]
    )

    assert app.main() == 0

    assert "dry_run=True" in events
    assert "xibo_construct" not in events


def test_cli_upload_mode_overrides_download_environment_without_graph(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch,
        tmp_path,
        env_mode="download",
        cli_args=["--yes", "--mode", "upload"],
    )
    for key in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SHAREPOINT_SITE_ID", "GRAPH_CALENDAR_USER_ID"):
        monkeypatch.delenv(key, raising=False)

    assert app.main() == 0

    assert events == ["save_delete_choice", "media_sync", "delete=False", "verified_client=False", "calendar_upload"]
    assert prompts == []


def test_upload_mode_attempts_calendar_after_media_failure(monkeypatch, tmp_path):
    events, _prompts, _stored_env = configure_main(
        monkeypatch, tmp_path, env_mode="upload"
    )
    monkeypatch.setattr(app, "run_media_sync", lambda *_args, **_kwargs: events.append("media_sync") or 2)

    assert app.main() == 2

    assert "media_sync" in events
    assert "calendar_upload" in events


def test_calendar_html_is_standalone_without_graph_keys(monkeypatch, tmp_path):
    events, prompts, _stored_env = configure_main(
        monkeypatch,
        tmp_path,
        env_mode="full",
        cli_args=["--yes", "--upload-calendar-html"],
    )
    for key in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SHAREPOINT_SITE_ID", "GRAPH_CALENDAR_USER_ID"):
        monkeypatch.delenv(key, raising=False)

    assert app.main() == 0

    assert events == ["calendar_html"]
    assert prompts == []


def test_removed_upload_calendar_flag_is_rejected(monkeypatch, tmp_path):
    configure_main(monkeypatch, tmp_path, cli_args=["--upload-calendar", "--yes"])

    with pytest.raises(SystemExit) as error:
        app.main()

    assert error.value.code == 2
