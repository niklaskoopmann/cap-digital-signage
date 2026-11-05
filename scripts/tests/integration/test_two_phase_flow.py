"""Integration tests for two-phase download and Xibo upload orchestration."""

from __future__ import annotations

from pathlib import Path
import json

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
    (tmp_path / ".env").write_text(f"SYNC_MODE={env_mode}\n")
    original_save = app.save_edits

    def save_env(directory, before, values, **kwargs):
        original_save(directory, before, values, **kwargs)
        events.append("save_delete_choice")
        if (directory / "sync-state.json").exists():
            stored_env.update(json.loads((directory / "sync-state.json").read_text()))

    monkeypatch.setattr(app, "save_edits", save_env)
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
        "phase1", "dry_run=False", "xibo_construct",
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

    assert events == ["phase1", "dry_run=False"]
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

    assert events == ["media_sync", "delete=False", "verified_client=False", "calendar_upload"]
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


@pytest.mark.parametrize("args,expected", [(["--yes"], True), (["--yes", "--delete"], True), (["--yes", "--no-delete"], False), (["--delete"], True), (["--no-delete"], False)])
def test_cli_deletion_choices_are_ephemeral_across_restarts(monkeypatch, tmp_path, args, expected):
    events, _, _ = configure_main(monkeypatch, tmp_path, env_mode="upload", cli_args=args, answers=[False])
    state_path = tmp_path / "sync-state.json"
    state_path.write_text('{"DELETE_REMOTE_NOT_LOCAL":"true","MANAGED_TAG":"keep"}')
    original = state_path.read_bytes()
    baseline = (tmp_path / ".env").read_bytes()
    assert app.main() == 0
    assert f"delete={expected}" in events
    assert state_path.read_bytes() == original
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])
    events.clear()
    assert app.main() == 0
    assert "delete=True" in events
    assert state_path.read_bytes() == original
    assert (tmp_path / ".env").read_bytes() == baseline


def test_interactive_deletion_survives_restart_without_copying_environment(monkeypatch, tmp_path):
    events, _, _ = configure_main(monkeypatch, tmp_path, env_mode="upload", cli_args=["--mode", "upload"], answers=[False, True])
    monkeypatch.setenv("MANAGED_TAG", "process-only")
    baseline = (tmp_path / ".env").read_bytes()
    assert app.main() == 0
    assert json.loads((tmp_path / "sync-state.json").read_text()) == {"DELETE_REMOTE_NOT_LOCAL": "true"}
    events.clear()
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])
    assert app.main() == 0
    assert "delete=True" in events
    assert (tmp_path / ".env").read_bytes() == baseline


def test_accepting_environment_deletion_default_preserves_saved_preference(monkeypatch, tmp_path):
    events, _, _ = configure_main(
        monkeypatch, tmp_path, env_mode="upload", cli_args=["--mode", "upload"], answers=[False, True]
    )
    state_path = tmp_path / "sync-state.json"
    state_path.write_text('{"DELETE_REMOTE_NOT_LOCAL":"false","MANAGED_TAG":"keep"}')
    original_state = state_path.read_bytes()
    monkeypatch.setenv("DELETE_REMOTE_NOT_LOCAL", "true")

    assert app.main() == 0
    assert "delete=True" in events
    assert state_path.read_bytes() == original_state

    events.clear()
    monkeypatch.delenv("DELETE_REMOTE_NOT_LOCAL")
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])
    assert app.main() == 0
    assert "delete=False" in events
    assert state_path.read_bytes() == original_state


@pytest.mark.parametrize("dry_source", ["argument", "baseline", "state", "environment"])
@pytest.mark.parametrize("config_only", [False, True])
def test_every_dry_run_entry_suppresses_all_configuration_mutation(monkeypatch, tmp_path, dry_source, config_only):
    import xibo_sync.env_io as env_io
    import xibo_sync.wizard as wizard

    args = ["--config"] if config_only else ["--mode", "upload"]
    if dry_source == "argument":
        args.append("--dry-run")
    configure_main(monkeypatch, tmp_path, env_mode="upload", cli_args=args, answers=[True, True])
    if dry_source == "baseline":
        with (tmp_path / ".env").open("a") as stream:
            stream.write("DRY_RUN=true\n")
    elif dry_source == "state":
        (tmp_path / "sync-state.json").write_text('{"DRY_RUN":"true"}')
    elif dry_source == "environment":
        monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(wizard, "prompt_edit", lambda key, current, **kwargs: {"MANAGED_TAG": "preview", "DRY_RUN": "false"}.get(key, current))
    monkeypatch.setattr(wizard, "prompt_password", lambda key, current: "preview-secret")
    displays = []
    monkeypatch.setattr(app, "ui_settings_table", lambda values, sources: displays.append((dict(values), dict(sources))))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    for owner, name in ((env_io, "atomic_write"), (env_io.tempfile, "mkstemp"), (env_io.os, "replace"), (env_io.os, "unlink")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: pytest.fail("dry-run mutated configuration"))
    assert app.main() == 0
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    if not config_only:
        values, sources = displays[-1]
        assert values["MANAGED_TAG"] == "preview"
        assert sources["MANAGED_TAG"] == "preview edit"
        assert values["DRY_RUN"] == "True"


@pytest.mark.parametrize("bad_file,content", [("sync-state.json", '{"CMS_CLIENT_SECRET":"private-canary"}'), ("secrets.json", '{broken'), (".env", 'CMS_CLIENT_SECRET=private-canary\n')])
def test_invalid_files_abort_before_prompts_or_external_calls(monkeypatch, tmp_path, bad_file, content):
    events, prompts, _ = configure_main(monkeypatch, tmp_path, env_mode="upload")
    errors = []
    monkeypatch.setattr(app, "ui_error", errors.append)
    (tmp_path / bad_file).write_text(content)
    assert app.main() == 2
    assert events == prompts == []
    assert "private-canary" not in " ".join(errors)


def test_failed_interactive_save_stops_before_network(monkeypatch, tmp_path):
    events, _, _ = configure_main(monkeypatch, tmp_path, cli_args=["--mode", "full"], answers=[False, True])

    def fail_save(*args, **kwargs):
        raise ValueError("Cannot save configuration")

    monkeypatch.setattr("xibo_sync.env_io.atomic_write", fail_save)
    assert app.main() == 2
    assert events == []


def test_startup_wizard_changes_apply_immediately_and_survive_restart(monkeypatch, tmp_path):
    import xibo_sync.wizard as wizard

    configure_main(monkeypatch, tmp_path, env_mode="upload", cli_args=["--mode", "upload"], answers=[True, False])
    monkeypatch.setenv("AUTH_MODE", "oauth")
    baseline = (tmp_path / ".env").read_bytes()
    edits = {"MANAGED_TAG": "wizard-tag", "CMS_CLIENT_ID": "wizard-id", "CMS_CLIENT_SECRET": "wizard-secret"}
    monkeypatch.setattr(wizard, "prompt_edit", lambda key, current, **kwargs: edits.get(key, current))
    monkeypatch.setattr(wizard, "prompt_password", lambda key, current: edits.get(key, current))
    configs = []
    monkeypatch.setattr(app, "run_media_sync", lambda cfg, *args, **kwargs: configs.append(cfg) or 0)
    assert app.main() == 0
    assert (configs[-1].managed_tag, configs[-1].cms_client_id, configs[-1].cms_client_secret) == ("wizard-tag", "wizard-id", "wizard-secret")
    assert json.loads((tmp_path / "sync-state.json").read_text())["MANAGED_TAG"] == "wizard-tag"
    assert json.loads((tmp_path / "secrets.json").read_text()) == {"CMS_CLIENT_ID": "wizard-id", "CMS_CLIENT_SECRET": "wizard-secret"}
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])
    assert app.main() == 0
    assert (configs[-1].managed_tag, configs[-1].cms_client_secret) == ("wizard-tag", "wizard-secret")
    assert (tmp_path / ".env").read_bytes() == baseline


def test_effective_display_renders_missing_credentials_empty(monkeypatch, tmp_path):
    from xibo_sync.env_io import CREDENTIAL_KEYS

    configure_main(monkeypatch, tmp_path, env_mode="upload")
    for key in CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    displays = []
    monkeypatch.setattr(app, "ui_settings_table", lambda values, sources: displays.append((dict(values), dict(sources))))

    assert app.main() == 0

    values, sources = displays[-1]
    for key in CREDENTIAL_KEYS:
        assert values[key] == ""
        assert sources.get(key, "default") == "default"


def test_effective_display_reloads_source_labels_after_startup_edits(monkeypatch, tmp_path):
    import xibo_sync.wizard as wizard

    configure_main(monkeypatch, tmp_path, env_mode="upload", cli_args=["--mode", "upload"], answers=[True, False])
    (tmp_path / ".env").write_text("SYNC_MODE=upload\nMANAGED_TAG=baseline-tag\n")
    monkeypatch.setattr(wizard, "prompt_edit", lambda key, current, **kwargs: "edited-tag" if key == "MANAGED_TAG" else current)
    monkeypatch.setattr(wizard, "prompt_password", lambda key, current: current)
    displays = []
    monkeypatch.setattr(app, "ui_settings_table", lambda values, sources: displays.append((dict(values), dict(sources))))
    assert app.main() == 0
    values, sources = displays[-1]
    assert values["MANAGED_TAG"] == "edited-tag"
    assert sources["MANAGED_TAG"] == "saved state"
    assert sources["SYNC_MODE"] == "CLI"


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("target", ["sync", "calendar-service", "all"])
def test_migration_cli_never_prompts_authenticates_or_runs_phases(monkeypatch, tmp_path, dry_run, target):
    root = tmp_path / "isolated-repo"
    scripts_dir = root / "scripts"
    service_dir = root / "services" / "calendar_render_service"
    scripts_dir.mkdir(parents=True)
    service_dir.mkdir(parents=True)
    for directory, identity in ((scripts_dir, "sync"), (service_dir, "service")):
        (directory / ".env").write_text(f"CMS_CLIENT_ID={identity}\nCMS_CLIENT_SECRET=private-{identity}\n")
    events, prompts, _ = configure_main(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--migrate-config", target, *(["--dry-run"] if dry_run else [])])
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert app.main(scripts_dir=scripts_dir) == 0
    assert events == prompts == []
    if dry_run:
        assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    else:
        for directory, name in ((scripts_dir, "sync"), (service_dir, "calendar-service")):
            assert (directory / "secrets.json").exists() == (target in (name, "all"))


@pytest.mark.parametrize("flag", [["--config"], ["--yes"], ["--mode", "upload"], ["--delete"], ["--no-delete"], ["--upload-calendar-html"]])
def test_migration_rejects_other_action_flags(monkeypatch, tmp_path, flag):
    events, prompts, _ = configure_main(monkeypatch, tmp_path, cli_args=["--migrate-config", "all", *flag])
    with pytest.raises(SystemExit) as failure:
        app.main()
    assert failure.value.code == 2
    assert events == prompts == []
