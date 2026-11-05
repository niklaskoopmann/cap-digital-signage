"""Unit tests for scripts/xibo_sync/env_io.py."""

from pathlib import Path

from xibo_sync.env_io import read_env_file, write_env_file
from xibo_sync.env_io import (
    CREDENTIAL_KEYS, SETTING_KEYS, atomic_write, load_settings, migrate_config,
    read_json_file, save_edits, write_json_file,
)
import json
import os
import stat
import pytest
import xibo_sync.env_io as env_io


@pytest.mark.parametrize("content", ["null", "[]", '"private-canary"', '{"CMS_CLIENT_ID":1}', '{"CMS_BASE_URL":"private-canary"}', '{"CMS_CLIENT_SECRET":"one","CMS_CLIENT_SECRET":"two"}', '{"CMS_CLIENT_SECRET":false}', '{broken'])
def test_invalid_secrets_never_fall_back_to_environment(tmp_path, content):
    (tmp_path / "secrets.json").write_text(content)
    with pytest.raises(ValueError) as failure:
        load_settings(tmp_path, environ={"CMS_CLIENT_SECRET": "override"})
    assert "private-canary" not in str(failure.value)
    assert "override" not in str(failure.value)


@pytest.mark.parametrize("name", [".env", "sync-state.json", "secrets.json"])
def test_unreadable_existing_configuration_fails_safely(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.write_text("{}" if name.endswith("json") else "CMS_BASE_URL=http://test\n")
    original_open = Path.open

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("private-canary")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ValueError) as failure:
        load_settings(tmp_path, environ={"CMS_CLIENT_SECRET": "override"})
    assert "private-canary" not in str(failure.value)


def test_all_layers_resolve_per_key_without_exporting_credentials(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('CMS_BASE_URL=http://baseline\nMANAGED_TAG=baseline\nSYNC_MODE=upload\n')
    write_json_file(tmp_path / "sync-state.json", {"MANAGED_TAG": "saved", "SYNC_MODE": "download"}, SETTING_KEYS)
    write_json_file(tmp_path / "secrets.json", {"CMS_CLIENT_ID": "file-id", "CMS_CLIENT_SECRET": "file-secret"}, CREDENTIAL_KEYS)
    monkeypatch.setenv("SYNC_MODE", "upload")
    before = dict(os.environ)
    values, sources = load_settings(tmp_path)
    assert {key: values[key] for key in ("CMS_BASE_URL", "MANAGED_TAG", "SYNC_MODE", "CMS_CLIENT_ID")} == {
        "CMS_BASE_URL": "http://baseline", "MANAGED_TAG": "saved", "SYNC_MODE": "upload", "CMS_CLIENT_ID": "file-id",
    }
    assert sources["MANAGED_TAG"] == "saved state"
    assert sources["CMS_CLIENT_ID"] == "secrets.json"
    assert sources["SYNC_MODE"] == "environment"
    assert dict(os.environ) == before


def test_save_prevalidates_both_destinations_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setattr(env_io, "atomic_write", lambda *args: pytest.fail("write before validation"))
    with pytest.raises(ValueError):
        save_edits(tmp_path, {}, {"MANAGED_TAG": "new", "UNKNOWN": "private-canary", "CMS_CLIENT_SECRET": "secret"})
    assert list(tmp_path.iterdir()) == []


def test_partial_save_reports_safe_retry_and_preserves_unrelated_values(tmp_path, monkeypatch):
    write_json_file(tmp_path / "secrets.json", {"GRAPH_CLIENT_ID": "keep"}, CREDENTIAL_KEYS)
    original_write = env_io.write_json_file

    def fail_secrets(path, values, allowed):
        if path.name == "secrets.json":
            raise ValueError("private-canary")
        original_write(path, values, allowed)

    with monkeypatch.context() as patch:
        patch.setattr(env_io, "write_json_file", fail_secrets)
        with pytest.raises(ValueError, match="some fields may already be saved") as failure:
            save_edits(tmp_path, {}, {"MANAGED_TAG": "saved", "CMS_CLIENT_SECRET": "private-canary"})
    assert "private-canary" not in str(failure.value)
    assert read_json_file(tmp_path / "sync-state.json", SETTING_KEYS) == {"MANAGED_TAG": "saved"}
    assert read_json_file(tmp_path / "secrets.json", CREDENTIAL_KEYS) == {"GRAPH_CLIENT_ID": "keep"}
    save_edits(tmp_path, {}, {"MANAGED_TAG": "saved", "CMS_CLIENT_SECRET": "private-canary"})
    assert read_json_file(tmp_path / "secrets.json", CREDENTIAL_KEYS)["GRAPH_CLIENT_ID"] == "keep"


@pytest.mark.parametrize("failure_stage", ["mkstemp", "fsync", "replace"])
def test_atomic_failure_preserves_existing_file_and_removes_temporary(tmp_path, monkeypatch, failure_stage):
    path = tmp_path / "secrets.json"
    path.write_text('{"CMS_CLIENT_SECRET":"old"}')
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("private-canary")

    owner = env_io.tempfile if failure_stage == "mkstemp" else env_io.os
    monkeypatch.setattr(owner, failure_stage, fail)
    with pytest.raises(ValueError) as failure:
        write_json_file(path, {"CMS_CLIENT_SECRET": "new"}, CREDENTIAL_KEYS)
    assert "private-canary" not in str(failure.value)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_private_permissions_new_and_existing_files(tmp_path):
    path = tmp_path / "secrets.json"
    atomic_write(path, "{}")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o400)
    atomic_write(path, "{}")
    assert stat.S_IMODE(path.stat().st_mode) == 0o400


@pytest.mark.parametrize("content", ['CMS_CLIENT_SECRET=first\nexport CMS_CLIENT_SECRET=second\n', 'CMS_CLIENT_SECRET="unterminated\n', 'CMS_CLIENT_SECRET\n'])
def test_migration_rejects_ambiguous_or_invalid_assignments_before_writes(tmp_path, content):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    source.write_text(content)
    with pytest.raises(ValueError):
        migrate_config(tmp_path, "sync")
    assert source.read_text() == content
    assert list(directory.iterdir()) == [source]


@pytest.mark.parametrize("target", ["sync", "calendar-service", "all"])
def test_migration_missing_sources_are_noops(tmp_path, target):
    migrate_config(tmp_path, target)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("assignment,value", [
    (b'CMS_CLIENT_SECRET="private"', "private"),
    (b'export CMS_CLIENT_SECRET="first\n\nlast"', "first\n\nlast"),
])
def test_migration_preserves_blank_lines_surrounding_removed_assignments(tmp_path, newline, assignment, value):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    prefix = b"# header" + newline + b" \t" + newline + newline
    suffix = b"\t " + newline + b"CMS_BASE_URL=http://cms" + newline
    source.write_bytes(prefix + assignment + newline + suffix)
    migrate_config(tmp_path, "sync")
    assert source.read_bytes() == prefix + suffix
    assert read_json_file(directory / "secrets.json", CREDENTIAL_KEYS) == {"CMS_CLIENT_SECRET": value}


def test_all_migration_keeps_distinct_identities_and_service_has_no_state(tmp_path):
    for relative, identity in (("scripts", "sync"), ("services/calendar_render_service", "service")):
        directory = tmp_path / relative
        directory.mkdir(parents=True)
        (directory / ".env").write_text(f'CMS_CLIENT_ID={identity}\nCMS_CLIENT_SECRET="{identity} # ${{LITERAL}}"\nDELETE_REMOTE_NOT_LOCAL=true\n')
    migrate_config(tmp_path, "all")
    for relative, identity in (("scripts", "sync"), ("services/calendar_render_service", "service")):
        directory = tmp_path / relative
        secrets = read_json_file(directory / "secrets.json", CREDENTIAL_KEYS)
        assert secrets == {"CMS_CLIENT_ID": identity, "CMS_CLIENT_SECRET": f"{identity} # ${{LITERAL}}"}
        if identity == "service":
            assert (directory / ".env").read_text() == "DELETE_REMOTE_NOT_LOCAL=true\n"
            assert not (directory / "sync-state.json").exists()
    migrate_config(tmp_path, "all")


@pytest.mark.parametrize("failure_stage", ["verification", "source_replace"])
def test_interrupted_migration_retains_source_and_retries_idempotently(tmp_path, monkeypatch, failure_stage):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    source.write_text("CMS_CLIENT_SECRET=private-canary\nDELETE_REMOTE_NOT_LOCAL=true\nCMS_BASE_URL=http://cms\n")
    before = source.read_bytes()
    original_read = env_io.read_json_file
    original_atomic = env_io.atomic_write

    def corrupt_verification(path, allowed):
        if path.name == "secrets.json" and path.exists():
            return {"CMS_CLIENT_SECRET": "unexpected"}
        return original_read(path, allowed)

    def fail_source(path, text):
        if path == source:
            raise ValueError("Cannot save configuration")
        original_atomic(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(env_io, "read_json_file", corrupt_verification if failure_stage == "verification" else original_read)
        patch.setattr(env_io, "atomic_write", fail_source)
        with pytest.raises(ValueError) as failure:
            migrate_config(tmp_path, "sync")
    assert "private-canary" not in str(failure.value)
    assert source.read_bytes() == before
    assert (directory / "secrets.json").exists()
    migrate_config(tmp_path, "sync")
    assert source.read_text() == "CMS_BASE_URL=http://cms\n"
    assert sorted(path.name for path in directory.iterdir()) == [".env", "secrets.json", "sync-state.json"]


@pytest.mark.parametrize("source", ["argument", "baseline", "state", "environment"])
def test_migration_dry_run_has_no_write_create_or_remove(tmp_path, monkeypatch, source):
    directory = tmp_path / "scripts"
    directory.mkdir()
    (directory / ".env").write_text("CMS_CLIENT_SECRET=private-canary\n" + ("DRY_RUN=true\n" if source == "baseline" else ""))
    if source == "state":
        write_json_file(directory / "sync-state.json", {"DRY_RUN": "true"}, SETTING_KEYS)
    if source == "environment":
        monkeypatch.setenv("DRY_RUN", "true")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    for owner, name in ((env_io, "atomic_write"), (env_io.tempfile, "mkstemp"), (env_io.os, "replace"), (env_io.os, "unlink")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: pytest.fail("dry-run configuration mutation"))
    migrate_config(tmp_path, "sync", dry_run=source == "argument")
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_layered_files_and_environment_are_not_mutated(tmp_path):
    (tmp_path / ".env").write_text("CMS_BASE_URL=http://baseline\nSYNC_MODE=upload\n")
    write_json_file(tmp_path / "sync-state.json", {"CMS_BASE_URL": "http://state"}, SETTING_KEYS)
    write_json_file(tmp_path / "secrets.json", {"CMS_CLIENT_SECRET": "file-secret"}, CREDENTIAL_KEYS)
    environment = {"CMS_BASE_URL": "http://environment", "CMS_CLIENT_SECRET": "process-secret"}
    original = dict(environment)
    values, sources = load_settings(tmp_path, environ=environment)
    assert values["CMS_BASE_URL"] == "http://environment"
    assert values["CMS_CLIENT_SECRET"] == "process-secret"
    assert sources["SYNC_MODE"] == ".env"
    assert environment == original


@pytest.mark.parametrize("content", ['[]', '{"SYNC_MODE": 3}', '{"UNKNOWN": "value"}',
                                     '{"CMS_CLIENT_SECRET": "hidden"}',
                                     '{"SYNC_MODE":"upload","SYNC_MODE":"full"}', '{broken'])
def test_invalid_state_fails_without_echoing_content(tmp_path, content):
    (tmp_path / "sync-state.json").write_text(content)
    with pytest.raises(ValueError) as failure:
        load_settings(tmp_path, environ={})
    assert content not in str(failure.value)


def test_legacy_credentials_rejected_even_with_environment(tmp_path):
    (tmp_path / ".env").write_text('CMS_CLIENT_SECRET="private-value"\n')
    with pytest.raises(ValueError, match="migrate-config sync") as failure:
        load_settings(tmp_path, environ={"CMS_CLIENT_SECRET": "override"})
    assert "private-value" not in str(failure.value)


def test_save_only_changed_fields_and_no_dry_run_writes(tmp_path):
    before = {"CMS_BASE_URL": "http://base", "CMS_CLIENT_SECRET": "old"}
    after = {**before, "CMS_CLIENT_SECRET": "new"}
    save_edits(tmp_path, before, after, dry_run=True)
    assert list(tmp_path.iterdir()) == []
    save_edits(tmp_path, before, after)
    assert not (tmp_path / "sync-state.json").exists()
    assert read_json_file(tmp_path / "secrets.json", CREDENTIAL_KEYS) == {"CMS_CLIENT_SECRET": "new"}


def test_migration_preserves_literals_comments_crlf_and_is_idempotent(tmp_path):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    source.write_bytes(b'# baseline\r\nCMS_BASE_URL=http://cms\r\nexport CMS_CLIENT_SECRET="ab # ${literal}"\r\nCMS_CLIENT_SECRET="ab # ${literal}"\r\nDELETE_REMOTE_NOT_LOCAL=false\r\n')
    migrate_config(tmp_path, "sync", dry_run=True)
    assert not (directory / "secrets.json").exists()
    migrate_config(tmp_path, "sync")
    assert source.read_bytes() == b'# baseline\r\nCMS_BASE_URL=http://cms\r\n'
    assert json.loads((directory / "secrets.json").read_text())["CMS_CLIENT_SECRET"] == "ab # ${literal}"
    assert json.loads((directory / "sync-state.json").read_text())["DELETE_REMOTE_NOT_LOCAL"] == "false"
    migrate_config(tmp_path, "sync")
    assert source.read_bytes() == b'# baseline\r\nCMS_BASE_URL=http://cms\r\n'


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_migration_preserves_inline_comments_for_all_assignment_forms(tmp_path, line_ending):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    lines = [
        "# keep this comment",
        "CMS_BASE_URL=http://cms",
        "export CMS_CLIENT_SECRET=plain # plain reason",
        "CMS_CLIENT_ID='quoted # literal' # quoted reason",
        "export GRAPH_CLIENT_ID=graph-id # exported reason",
        "GRAPH_CLIENT_SECRET=repeated # first duplicate reason",
        "GRAPH_CLIENT_SECRET=repeated # second duplicate reason",
    ]
    source.write_bytes((line_ending.join(lines) + line_ending).encode("utf-8"))

    migrate_config(tmp_path, "sync")

    expected_lines = [
        "# keep this comment",
        "CMS_BASE_URL=http://cms",
        "# plain reason",
        "# quoted reason",
        "# exported reason",
        "# first duplicate reason",
        "# second duplicate reason",
    ]
    assert source.read_bytes() == (line_ending.join(expected_lines) + line_ending).encode("utf-8")
    secrets = json.loads((directory / "secrets.json").read_text(encoding="utf-8"))
    assert secrets == {
        "CMS_CLIENT_SECRET": "plain",
        "CMS_CLIENT_ID": "quoted # literal",
        "GRAPH_CLIENT_ID": "graph-id",
        "GRAPH_CLIENT_SECRET": "repeated",
    }


def test_all_migration_preflights_both_installations(tmp_path):
    sync = tmp_path / "scripts"
    service = tmp_path / "services" / "calendar_render_service"
    sync.mkdir()
    service.mkdir(parents=True)
    (sync / ".env").write_text("CMS_CLIENT_SECRET=sync-secret\n")
    (service / ".env").write_text("CMS_CLIENT_SECRET=service-secret\n")
    write_json_file(service / "secrets.json", {"CMS_CLIENT_SECRET": "conflict"}, CREDENTIAL_KEYS)
    with pytest.raises(ValueError, match="conflict"):
        migrate_config(tmp_path, "all")
    assert not (sync / "secrets.json").exists()
    assert "sync-secret" in (sync / ".env").read_text()


def test_failed_destination_retains_source_and_cleans_temp(tmp_path, monkeypatch):
    directory = tmp_path / "scripts"
    directory.mkdir()
    source = directory / ".env"
    source.write_text("CMS_CLIENT_SECRET=private\n")
    original = source.read_bytes()
    def fail_replace(*args):
        raise OSError("synthetic failure")
    monkeypatch.setattr("xibo_sync.env_io.os.replace", fail_replace)
    with pytest.raises(ValueError, match="Cannot save"):
        migrate_config(tmp_path, "sync")
    assert source.read_bytes() == original
    assert list(directory.iterdir()) == [source]


def test_read_env_file_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "missing.env") == {}


def test_read_env_file_parses_values_and_skips_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# a full-line comment",
                "",
                "CMS_BASE_URL=http://192.168.1.1",
                "DRY_RUN=false  # inline comment",
                "not-a-kv-line",
            ]
        ),
        encoding="utf-8",
    )

    values = read_env_file(env_path)

    assert values == {
        "CMS_BASE_URL": "http://192.168.1.1",
        "DRY_RUN": "false",
    }


def test_write_env_file_creates_new_file_sorted(tmp_path: Path) -> None:
    env_path = tmp_path / "new.env"

    write_env_file(env_path, {"B_KEY": "2", "A_KEY": "1"})

    assert env_path.read_text(encoding="utf-8").splitlines() == ["A_KEY=1", "B_KEY=2"]


def test_write_env_file_preserves_comments_and_updates_known_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# keep this comment",
                "CMS_BASE_URL=http://old",
                "UNRELATED=stay",
            ]
        ),
        encoding="utf-8",
    )

    write_env_file(env_path, {"CMS_BASE_URL": "http://new", "NEW_KEY": "value"})

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# keep this comment"
    assert "CMS_BASE_URL=http://new" in lines
    assert "UNRELATED=stay" in lines
    assert "NEW_KEY=value" in lines
